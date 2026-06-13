"""Scraping Module (Section 3b).

Visits candidate company sites with Playwright (headless Chromium + stealth),
falling back to BeautifulSoup parsing of fetched HTML. Enforces robots.txt
(FR-11), the IndiaMart/TradeIndia exclusion (FR-12), the 3s per-domain rate
limit (FR-07), the 30s page-load timeout (FR-30), bounded concurrency (NFR-03),
UA/proxy rotation (NFR-06), and decodes Cloudflare-obfuscated emails (FR-06).

Output per domain:
``{domain, company_name, address, phones[], emails[], linkedin_url,
   social_links{}, raw_text, scrape_blocked}``
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
import urllib.robotparser
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from config import Config
from utils import is_excluded_directory

logger = logging.getLogger(__name__)

PATHS = ["/", "/contact", "/contact-us", "/about", "/about-us", "/team"]
ROBOTS_CHECK_PATHS = ["/", "/contact", "/about"]  # Section 8.1
PAGE_TIMEOUT_MS = 30_000  # FR-30
MIN_DOMAIN_DELAY = 3.0  # FR-07 hard minimum (NOT configurable below 3s)

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
PHONE_MOBILE_RE = re.compile(r"(?:\+91|0)?[6-9]\d{9}")
PHONE_LANDLINE_RE = re.compile(r"0\d{2,4}[-\s]?\d{6,8}")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

BLOCK_MARKERS = (
    "captcha",
    "cloudflare",
    "attention required",
    "verify you are human",
    "checking your browser",
)


def decode_cfemail(encoded: str) -> str | None:
    """Decode a Cloudflare ``data-cfemail`` hex string (FR-06).

    First byte is the XOR key; XOR each subsequent byte against it.
    """
    try:
        key = int(encoded[:2], 16)
        return "".join(
            chr(int(encoded[i : i + 2], 16) ^ key) for i in range(2, len(encoded), 2)
        )
    except (ValueError, IndexError):
        return None


class ProxyRotator:
    """Round-robin proxy rotation per domain with failed-proxy exclusion (NFR-06)."""

    def __init__(self, proxies: list[dict]):
        self.proxies = proxies
        self.failed: set[int] = set()
        self._idx = 0

    def next(self) -> dict | None:
        available = [i for i in range(len(self.proxies)) if i not in self.failed]
        if not available:
            return None
        idx = available[self._idx % len(available)]
        self._idx += 1
        return {"_idx": idx, **self.proxies[idx]}

    def mark_failed(self, idx: int) -> None:
        self.failed.add(idx)


class Scraper:
    """Manages a single Playwright browser and scrapes candidate domains."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.semaphore = asyncio.Semaphore(cfg.max_concurrent_browsers)
        self.proxy_rotator = ProxyRotator(cfg.proxies)
        self._robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last_request: dict[str, float] = {}
        self._playwright = None
        self._browser = None

    async def __aenter__(self) -> "Scraper":
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    # -- robots.txt (FR-11, Section 8.1) ----------------------------------- #

    async def _allowed_by_robots(self, domain: str) -> bool:
        if domain in self._robots_cache:
            rp = self._robots_cache[domain]
        else:
            rp = await self._fetch_robots(domain)
            self._robots_cache[domain] = rp
        if rp is None:
            return True  # no robots.txt or unreachable -> allowed
        for path in ROBOTS_CHECK_PATHS:
            url = f"https://{domain}{path}"
            if not (rp.can_fetch("*", url) and rp.can_fetch("python-httpx", url)):
                return False
        return True

    async def _fetch_robots(self, domain: str):
        url = f"https://{domain}/robots.txt"
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                resp = await client.get(url)
            if resp.status_code != 200 or not resp.text.strip():
                return None
            rp = urllib.robotparser.RobotFileParser()
            rp.parse(resp.text.splitlines())
            return rp
        except (httpx.HTTPError, ValueError):
            return None

    # -- per-domain rate limit (FR-07) ------------------------------------- #

    async def _respect_rate_limit(self, domain: str) -> None:
        last = self._last_request.get(domain)
        if last is not None:
            elapsed = time.monotonic() - last
            wait = max(MIN_DOMAIN_DELAY, random.uniform(2, 5)) - elapsed
            if wait > 0:
                await asyncio.sleep(wait)
        self._last_request[domain] = time.monotonic()

    # -- public API -------------------------------------------------------- #

    async def scrape_all(self, candidates: list[dict]) -> list[dict]:
        tasks = [self._scrape_with_semaphore(c) for c in candidates]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out: list[dict] = []
        for c, r in zip(candidates, results):
            if isinstance(r, Exception):
                logger.error("Unhandled scrape error for %s: %s", c["domain"], r)
                continue
            if r is not None:
                out.append(r)
        return out

    async def _scrape_with_semaphore(self, candidate: dict) -> dict | None:
        async with self.semaphore:
            return await self._scrape_domain(candidate)

    async def _scrape_domain(self, candidate: dict) -> dict | None:
        domain = candidate["domain"]
        if is_excluded_directory(domain):  # FR-12
            logger.info("Skipping excluded directory domain: %s", domain)
            return None
        if not await self._allowed_by_robots(domain):  # FR-11
            logger.info("robots.txt disallows scraping %s — skipping (compliance).", domain)
            return None

        data = {
            "domain": domain,
            "company_name": None,
            "address": None,
            "phones": [],
            "emails": [],
            "linkedin_url": None,
            "social_links": {},
            "raw_text": "",
            "scrape_blocked": False,
            "discovery_source": candidate.get("discovery_source", ""),
            "website": f"https://{domain}",
        }

        proxy = self.proxy_rotator.next()
        context_kwargs = {"user_agent": random.choice(USER_AGENTS)}
        if proxy:
            context_kwargs["proxy"] = {k: v for k, v in proxy.items() if k != "_idx"}

        context = None
        timed_out_paths = 0
        try:
            context = await self._browser.new_context(**context_kwargs)
            await self._apply_stealth(context)
            page = await context.new_page()

            for path in PATHS:
                url = urljoin(f"https://{domain}", path)
                await self._respect_rate_limit(domain)
                try:
                    resp = await page.goto(url, timeout=PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
                except Exception as exc:  # noqa: BLE001 - playwright TimeoutError et al.
                    logger.warning("Page load timeout/error for %s%s: %s", domain, path, exc)
                    timed_out_paths += 1
                    continue

                if resp is not None and resp.status == 403:
                    logger.warning("HTTP 403 on %s%s — marking blocked.", domain, path)
                    data["scrape_blocked"] = True
                    break

                try:
                    await page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT_MS)
                except Exception:  # noqa: BLE001 - networkidle may not settle
                    pass
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(0.5)

                html = await page.content()
                shadow_text = await self._extract_shadow_text(page)

                if self._is_blocked(html):
                    logger.warning("Block/CAPTCHA page detected on %s%s.", domain, path)
                    data["scrape_blocked"] = True
                    break

                self._parse_html(html, shadow_text, url, data)

                if data["emails"]:  # FR-05: stop early once a verified email is found
                    logger.info("Email found for %s on %s — stopping path traversal.", domain, path)
                    break

            if timed_out_paths == len(PATHS):
                logger.warning("All paths timed out for %s — skipping domain.", domain)
                return None
        finally:
            if context:
                await context.close()

        # NFR-06: a proxy connection failure marks the proxy dead for the run.
        data["phones"] = list(dict.fromkeys(data["phones"]))
        data["emails"] = list(dict.fromkeys(data["emails"]))
        return data

    async def _apply_stealth(self, context) -> None:
        try:
            from playwright_stealth import stealth_async  # type: ignore

            page = await context.new_page()
            await stealth_async(page)
            await page.close()
        except Exception:  # noqa: BLE001 - stealth optional, never fatal
            pass

    async def _extract_shadow_text(self, page) -> str:
        """Pierce shadow roots to recover text/emails hidden inside them (Table 6)."""
        try:
            return await page.evaluate(
                """() => {
                    let out = [];
                    const walk = (root) => {
                        root.querySelectorAll('*').forEach(el => {
                            if (el.shadowRoot) {
                                out.push(el.shadowRoot.textContent || '');
                                walk(el.shadowRoot);
                            }
                        });
                    };
                    walk(document);
                    return out.join(' ');
                }"""
            )
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _is_blocked(html: str) -> bool:
        low = html.lower()
        return any(m in low for m in BLOCK_MARKERS)

    def _parse_html(self, html: str, shadow_text: str, url: str, data: dict) -> None:
        soup = BeautifulSoup(html, "lxml")

        # Cloudflare-obfuscated emails (FR-06)
        for el in soup.select(".__cf_email__[data-cfemail], [data-cfemail]"):
            decoded = decode_cfemail(el.get("data-cfemail", ""))
            if decoded and EMAIL_RE.fullmatch(decoded):
                data["emails"].append(decoded)

        # mailto: links (Table 6)
        for a in soup.select('a[href^="mailto:"]'):
            addr = a["href"][len("mailto:") :].split("?")[0].strip()
            if EMAIL_RE.fullmatch(addr):
                data["emails"].append(addr)

        text = soup.get_text(" ", strip=True) + " " + shadow_text
        data["raw_text"] = (data["raw_text"] + " " + text).strip()

        data["emails"].extend(EMAIL_RE.findall(text))
        data["phones"].extend(self._extract_phones(text))

        if not data["company_name"]:
            data["company_name"] = self._extract_company_name(soup)
        if not data["address"]:
            data["address"] = self._extract_address(soup)

        for a in soup.find_all("a", href=True):
            href = a["href"]
            low = href.lower()
            if "linkedin.com/company/" in low and not data["linkedin_url"]:
                data["linkedin_url"] = href
            elif any(s in low for s in ("twitter.com", "x.com")) and "twitter" not in data["social_links"]:
                data["social_links"]["twitter"] = href
            elif "facebook.com" in low and "facebook" not in data["social_links"]:
                data["social_links"]["facebook"] = href
            elif "instagram.com" in low and "instagram" not in data["social_links"]:
                data["social_links"]["instagram"] = href

    @staticmethod
    def _extract_phones(text: str) -> list[str]:
        phones = PHONE_MOBILE_RE.findall(text)
        phones += PHONE_LANDLINE_RE.findall(text)
        return [p.strip() for p in phones]

    @staticmethod
    def _extract_company_name(soup: BeautifulSoup) -> str | None:
        import json as _json

        for script in soup.find_all("script", type="application/ld+json"):
            try:
                payload = _json.loads(script.string or "")
            except (ValueError, TypeError):
                continue
            entries = payload if isinstance(payload, list) else [payload]
            for entry in entries:
                if isinstance(entry, dict) and entry.get("name"):
                    return str(entry["name"]).strip()
        if soup.title and soup.title.string:
            return soup.title.string.strip()
        h1 = soup.find("h1")
        if h1:
            return h1.get_text(strip=True)
        return None

    @staticmethod
    def _extract_address(soup: BeautifulSoup) -> str | None:
        import json as _json

        for script in soup.find_all("script", type="application/ld+json"):
            try:
                payload = _json.loads(script.string or "")
            except (ValueError, TypeError):
                continue
            entries = payload if isinstance(payload, list) else [payload]
            for entry in entries:
                if isinstance(entry, dict):
                    addr = entry.get("address")
                    if isinstance(addr, dict):
                        parts = [
                            addr.get("streetAddress"),
                            addr.get("addressLocality"),
                            addr.get("addressRegion"),
                            addr.get("postalCode"),
                        ]
                        joined = ", ".join(p for p in parts if p)
                        if joined:
                            return joined
                    elif isinstance(addr, str) and addr.strip():
                        return addr.strip()
        node = soup.find(string=re.compile(r"address", re.I))
        if node and node.parent:
            return node.parent.get_text(" ", strip=True)[:200]
        return None
