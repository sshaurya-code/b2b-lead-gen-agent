"""Buying Signal Detector (Section 3d).

Two signal types:
  1. Website keyword signals (FR-16) scanned from scraped ``raw_text``.
  2. News / expansion signals (FR-17) via a per-company SearchProvider query.

Optional LLM confidence scoring (LLM_SIGNAL_SCORING) refines ambiguous website
signals; disabled by default.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import httpx

from config import Config
from providers import QuotaExhaustedError, SearchProvider

logger = logging.getLogger(__name__)

# Website keyword signals (Section 3d, Signal Type 1).
KEYWORD_SIGNALS = [
    "request for quotation", "rfq", "request a quote", "get a quote",
    "tender", "e-tender", "gem portal", "government tender",
    "bulk order", "bulk supply", "bulk purchase",
    "we are looking for suppliers", "seeking suppliers", "vendor registration",
    "sourcing", "procurement", "purchase enquiry",
    "import", "importing", "we import",
    "supply chain", "supply requirement",
    "price list", "rate list", "price on request",
    "distributor wanted", "dealer enquiry", "dealership",
    "new project", "upcoming project", "project requirement",
]

SNIPPET_RADIUS = 25  # ~50-char context window around a match

_MONTHS = {
    m: i for i, m in enumerate(
        ["january", "february", "march", "april", "may", "june", "july",
         "august", "september", "october", "november", "december"], start=1)
}
_MONTHS.update({m[:3]: i for m, i in list(_MONTHS.items())})

_RE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_RE_DD_MONTH_YYYY = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b")
_RE_MONTH_YYYY = re.compile(r"\b([A-Za-z]{3,9})\s+(\d{4})\b")


def _to_iso(year: int, month: int, day: int) -> str | None:
    try:
        return datetime(year, month, day, tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None


def parse_first_date(text: str) -> str | None:
    """Extract the first date from a snippet (DD Month YYYY / Month YYYY / YYYY-MM-DD)."""
    m = _RE_ISO.search(text)
    if m:
        iso = _to_iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if iso:
            return iso
    m = _RE_DD_MONTH_YYYY.search(text)
    if m and m.group(2).lower() in _MONTHS:
        iso = _to_iso(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
        if iso:
            return iso
    m = _RE_MONTH_YYYY.search(text)
    if m and m.group(1).lower() in _MONTHS:
        iso = _to_iso(int(m.group(2)), _MONTHS[m.group(1).lower()], 1)
        if iso:
            return iso
    return None


def _news_confidence(signal_date: str | None, run_dt: datetime) -> str:
    if not signal_date:
        return "low"
    try:
        dt = datetime.fromisoformat(signal_date.replace("Z", "+00:00"))
    except ValueError:
        return "low"
    days = (run_dt - dt).days
    if days <= 90:
        return "high"
    if days <= 365:
        return "medium"
    return "low"


class SignalDetector:
    def __init__(self, cfg: Config, provider: SearchProvider, http_client: httpx.AsyncClient):
        self.cfg = cfg
        self.provider = provider
        self.http_client = http_client

    async def detect(
        self, company_name: str | None, website: str | None, raw_text: str, run_dt: datetime
    ) -> list[dict]:
        signals = self._website_keyword_signals(raw_text, website)
        if company_name:
            signals.extend(await self._news_signals(company_name, run_dt))
        if self.cfg.llm_signal_scoring:
            await self._apply_llm_scoring(signals, raw_text)
        return signals

    def _website_keyword_signals(self, raw_text: str, website: str | None) -> list[dict]:
        if not raw_text:
            return []
        low = raw_text.lower()
        matches: list[dict] = []
        matched_keywords = 0
        for kw in KEYWORD_SIGNALS:
            idx = low.find(kw)
            if idx == -1:
                continue
            matched_keywords += 1
            start = max(0, idx - SNIPPET_RADIUS)
            end = min(len(raw_text), idx + len(kw) + SNIPPET_RADIUS)
            matches.append({
                "type": "website_keyword",
                "text_snippet": raw_text[start:end].strip(),
                "signal_date": None,  # website text carries no date
                "confidence": "medium",  # finalised below once count is known
                "source_url": website,  # the company website it was scraped from
                "source_query": None,
            })
        # FR-16: high if >=3 distinct keyword matches, else medium.
        confidence = "high" if matched_keywords >= 3 else "medium"
        for m in matches:
            m["confidence"] = confidence
        return matches

    async def _news_signals(self, company_name: str, run_dt: datetime) -> list[dict]:
        # Prefer NewsAPI when configured (exact dates + article URLs); else fall
        # back to the configured SEARCH_PROVIDER.
        if self.cfg.newsapi_api_key:
            return await self._news_signals_via_newsapi(company_name, run_dt)
        query = (
            f'"{company_name}" expansion OR "new project" OR tender India '
            f"2024 OR 2025 OR 2026"
        )
        try:
            results = await self.provider.search(query, max_results=5)
        except QuotaExhaustedError:
            logger.warning("Search quota exhausted during news signal lookup for %s.", company_name)
            return []
        signals: list[dict] = []
        for r in results:
            snippet = r.get("snippet", "")
            signal_date = parse_first_date(snippet)
            if signal_date:
                m = _RE_ISO.search(snippet) or _RE_DD_MONTH_YYYY.search(snippet) or _RE_MONTH_YYYY.search(snippet)
                pos = m.start() if m else 0
                start = max(0, pos - SNIPPET_RADIUS)
                text_snippet = snippet[start : start + 2 * SNIPPET_RADIUS].strip()
            else:
                text_snippet = snippet[: 2 * SNIPPET_RADIUS].strip()
            signals.append({
                "type": "news_expansion",
                "text_snippet": text_snippet,
                "signal_date": signal_date,
                "confidence": _news_confidence(signal_date, run_dt),
                "source_url": r.get("url"),  # the news/search result it came from
                "source_query": query,
            })
        return signals

    async def _news_signals_via_newsapi(self, company_name: str, run_dt: datetime) -> list[dict]:
        """News/expansion signals from NewsAPI (/v2/everything). Exact publish dates."""
        query = f'"{company_name}" AND (expansion OR tender OR "new project" OR project OR factory)'
        params = {
            "q": query,
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": 5,
            "apiKey": self.cfg.newsapi_api_key,
        }
        try:
            resp = await self.http_client.get(
                "https://newsapi.org/v2/everything", params=params, timeout=10.0
            )
        except Exception as exc:  # noqa: BLE001 - news is best-effort
            logger.warning("NewsAPI request failed for %s: %s", company_name, exc)
            return []
        if resp.status_code != 200:
            logger.error("NewsAPI HTTP %s: %s", resp.status_code, resp.text[:200])
            return []
        articles = resp.json().get("articles", []) or []
        signals: list[dict] = []
        for a in articles:
            published = a.get("publishedAt")  # already ISO8601 (e.g. 2026-05-12T08:00:00Z)
            text = a.get("title") or a.get("description") or ""
            source = (a.get("source") or {}).get("name") or "NewsAPI"
            signals.append({
                "type": "news_expansion",
                "text_snippet": text[: 2 * SNIPPET_RADIUS].strip(),
                "signal_date": published,
                "confidence": _news_confidence(published, run_dt),
                "source_url": a.get("url"),
                "source_query": f"NewsAPI: {query} (via {source})",
            })
        return signals

    async def _apply_llm_scoring(self, signals: list[dict], raw_text: str) -> None:
        """Optionally refine ambiguous website-keyword confidence via an LLM."""
        ambiguous = [s for s in signals if s["type"] == "website_keyword" and s["confidence"] == "medium"]
        if not ambiguous:
            return
        verdict = await self._llm_classify(raw_text[:1500])
        if verdict in ("high", "medium", "low"):
            for s in ambiguous:
                s["confidence"] = verdict

    async def _llm_classify(self, text: str) -> str | None:
        key = self.cfg.llm_api_key or ""
        prompt = (
            "You assess B2B buying intent. Given the website text, reply with a "
            "single word — high, medium, or low — for how strongly it signals "
            "active procurement/sourcing intent.\n\n" + text
        )
        try:
            if key.startswith("sk-ant-"):
                resp = await self.http_client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                             "content-type": "application/json"},
                    json={"model": self.cfg.llm_model, "max_tokens": 8,
                          "messages": [{"role": "user", "content": prompt}]},
                    timeout=10.0,
                )
                out = resp.json()["content"][0]["text"]
            else:
                resp = await self.http_client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json={"model": self.cfg.llm_model, "max_tokens": 8,
                          "messages": [{"role": "user", "content": prompt}]},
                    timeout=10.0,
                )
                out = resp.json()["choices"][0]["message"]["content"]
            out = out.strip().lower()
            for level in ("high", "medium", "low"):
                if level in out:
                    return level
        except Exception as exc:  # noqa: BLE001 - LLM is best-effort
            logger.warning("LLM signal scoring failed (keeping keyword confidence): %s", exc)
        return None
