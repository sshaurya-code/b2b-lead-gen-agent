"""Shared utilities used across modules.

- ``normalize_domain``: the single shared normalisation function required by
  FR-03 / FR-14 (used by both Discovery and the Data Layer).
- PII-redacting logging (FR-33 / NFR-09): emails and phones are masked in log
  output only; stored data is unaffected.
- ``request_with_retries``: exponential-backoff HTTP retry honouring
  ``Retry-After`` (FR-28 / FR-29 / NFR-04).
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:  # httpx imported lazily so pure utils stay dependency-free
    import httpx

# --------------------------------------------------------------------------- #
# Domain normalisation (FR-03, FR-14)
# --------------------------------------------------------------------------- #

# Directories used only as Google search targets — never scraped directly
# (FR-12 / §8.6 for IndiaMart/TradeIndia; JustDial added for the same ToS reason).
EXCLUDED_DIRECTORY_DOMAINS = ("indiamart.com", "tradeindia.com", "justdial.com")


def normalize_domain(url: str) -> str:
    """Reduce any URL to a bare root domain for deduplication.

    Strips scheme, path, query, fragment and a leading ``www.``; lowercases;
    strips a trailing slash. Shared by the Discovery Module and the Data Layer
    so both apply identical rules (FR-14).
    """
    if not url:
        return ""
    raw = url.strip()
    if "://" not in raw:
        raw = "http://" + raw
    netloc = urlparse(raw).netloc or ""
    netloc = netloc.split("@")[-1]  # drop any userinfo
    netloc = netloc.split(":")[0]  # drop port
    netloc = netloc.lower().strip().rstrip("/")
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def is_excluded_directory(domain: str) -> bool:
    """True if the domain is IndiaMart/TradeIndia, which must never be scraped (FR-12)."""
    return any(domain == d or domain.endswith("." + d) for d in EXCLUDED_DIRECTORY_DOMAINS)


# --------------------------------------------------------------------------- #
# Qualification-signal helpers (GSTIN, Indian mobile, directory markers)
# --------------------------------------------------------------------------- #

# Standard 15-char GSTIN: 2-digit state code, 5 letters, 4 digits, 1 letter,
# 1 entity digit/letter, 'Z', 1 checksum digit/letter.
_GSTIN_RE = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")


def extract_gstin(text: str | None) -> str | None:
    """Return the first GSTIN found in text (e.g. from a company's own website)."""
    if not text:
        return None
    m = _GSTIN_RE.search(text.upper())
    return m.group(0) if m else None


def is_indian_mobile(phone: str | None) -> bool:
    """True if the number normalises to a 10-digit Indian mobile (starts 6-9)."""
    if not phone:
        return False
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return len(digits) == 10 and digits[0] in "6789"


def directory_markers(text: str | None) -> tuple[bool, list[str]]:
    """Detect directory listing + verified tag from text (website/links or a
    Google result snippet — never from scraping the directory directly).

    Returns ``(indiamart_verified, sources)`` where sources is a subset of
    ["indiamart", "justdial"].
    """
    low = (text or "").lower()
    sources: list[str] = []
    if "indiamart.com" in low or "trustseal" in low:
        sources.append("indiamart")
    if "justdial.com" in low or "jdmart.com" in low:
        sources.append("justdial")
    verified = any(
        marker in low
        for marker in ("trustseal", "verified exporter", "verified supplier", "indiamart verified")
    )
    return verified, sources


# --------------------------------------------------------------------------- #
# PII-redacting logging (FR-33, NFR-09)
# --------------------------------------------------------------------------- #

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(r"(?<!\w)(\+91|0)?[6-9]\d{9}(?!\w)")


def redact_pii(text: str) -> str:
    """Mask emails and phone numbers in a string for safe logging."""
    text = _EMAIL_RE.sub("[email redacted]", text)
    text = _PHONE_RE.sub("[phone redacted]", text)
    return text


class PIIRedactionFilter(logging.Filter):
    """Logging filter that masks PII in the rendered message (FR-33).

    Masking happens at the logging layer only — the data in storage is untouched.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_pii(record.msg)
        if record.args:
            record.args = tuple(
                redact_pii(a) if isinstance(a, str) else a for a in record.args
            )
        return True


def setup_logging(output_dir: Path, level: int = logging.INFO) -> logging.Logger:
    """Configure root logging to ``agent.log`` (in OUTPUT_DIR) and stderr (NFR-09)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "agent.log"

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    pii_filter = PIIRedactionFilter()

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(fmt)
    file_handler.addFilter(pii_filter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    stream_handler.addFilter(pii_filter)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(stream_handler)
    return root


# --------------------------------------------------------------------------- #
# HTTP retry with exponential backoff (FR-28, FR-29, NFR-04)
# --------------------------------------------------------------------------- #

RETRY_BACKOFFS = (1.0, 2.0, 4.0)  # waits after the 1st, 2nd, 3rd failure


async def request_with_retries(
    client: "httpx.AsyncClient",
    method: str,
    url: str,
    *,
    logger: logging.Logger,
    max_retries: int = 3,
    **kwargs,
) -> "httpx.Response | None":
    """Issue an HTTP request with exponential backoff.

    Retries on connection/timeout errors and on HTTP 429. A ``Retry-After``
    header (on a 429) overrides the computed backoff wait. On HTTP 401/403 the
    response is returned immediately so callers can apply specific handling
    (e.g. invalid Apollo key). After exhausting retries, logs at ERROR and
    returns ``None`` so the caller can continue the pipeline.
    """
    import httpx

    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = await client.request(method, url, **kwargs)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            if attempt == max_retries:
                logger.error("Request to %s failed after retries: %s", url, exc)
                return None
            await asyncio.sleep(RETRY_BACKOFFS[min(attempt, len(RETRY_BACKOFFS) - 1)])
            continue

        if resp.status_code == 429:
            if attempt == max_retries:
                logger.error("Request to %s rate-limited (429) after retries", url)
                return resp
            retry_after = resp.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                wait = float(retry_after)
            else:
                wait = RETRY_BACKOFFS[min(attempt, len(RETRY_BACKOFFS) - 1)]
            logger.warning("429 from %s — backing off %.1fs (attempt %d)", url, wait, attempt + 1)
            await asyncio.sleep(wait)
            continue

        return resp

    if last_exc:
        logger.error("Request to %s ultimately failed: %s", url, last_exc)
    return None
