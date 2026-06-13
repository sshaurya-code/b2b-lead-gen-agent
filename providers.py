"""Search provider abstraction (Section 3a, FR-31, FR-32).

A ``SearchProvider`` ABC with three concrete implementations — ``google_cse``,
``serpapi`` and ``brave`` — all exposing the same ``search`` interface so the
Discovery Module (and the Buying Signal Detector's news query) can swap
providers with zero downstream code changes (FR-32). ``get_provider`` routes on
``SEARCH_PROVIDER`` and halts on an unrecognised value (FR-31, enforced earlier
in config.load_config).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import httpx

from config import Config
from utils import request_with_retries


class SearchProviderError(Exception):
    """Base class for provider-level failures."""


class QuotaExhaustedError(SearchProviderError):
    """Daily/plan quota exhausted (e.g. Google CSE 429 / 403 quotaExceeded)."""


class ProviderDeprecatedError(SearchProviderError):
    """Provider endpoint is gone (e.g. Google CSE post-2027, HTTP 410)."""


# Google CSE pagination start values (FR-02): up to 10 pages, 100 results.
GOOGLE_CSE_START_VALUES = [1, 11, 21, 31, 41, 51, 61, 71, 81, 91]


class SearchProvider(ABC):
    """Uniform search interface. Returns dicts: ``{url, snippet, query}``."""

    name: str = "base"

    def __init__(self, cfg: Config, client: httpx.AsyncClient, logger: logging.Logger):
        self.cfg = cfg
        self.client = client
        self.logger = logger

    @abstractmethod
    async def search(self, query: str, *, max_results: int = 100) -> list[dict]:
        """Return up to ``max_results`` results for ``query``."""
        raise NotImplementedError

    @staticmethod
    def _result(url: str, snippet: str, query: str) -> dict:
        return {"url": url, "snippet": snippet or "", "query": query}


class GoogleCSEProvider(SearchProvider):
    """Google Custom Search JSON API (default; deprecated 2027-01-01)."""

    name = "google_cse"
    BASE_URL = "https://www.googleapis.com/customsearch/v1"

    async def search(self, query: str, *, max_results: int = 100) -> list[dict]:
        results: list[dict] = []
        for start in GOOGLE_CSE_START_VALUES:
            if len(results) >= max_results:
                break
            params = {
                "key": self.cfg.google_cse_api_key,
                "cx": self.cfg.google_cse_id,
                "q": query,
                "start": start,
                "num": 10,
            }
            resp = await request_with_retries(
                self.client, "GET", self.BASE_URL, logger=self.logger, params=params,
                timeout=10.0,
            )
            if resp is None:
                break
            if resp.status_code == 410:
                raise ProviderDeprecatedError(
                    "Google CSE returned HTTP 410 — the API has shut down. "
                    "See Section 8.5: set SEARCH_PROVIDER=serpapi or brave."
                )
            if resp.status_code in (403, 429):
                body = resp.text.lower()
                if resp.status_code == 429 or "quotaexceeded" in body or "quota" in body:
                    raise QuotaExhaustedError(
                        f"Google CSE quota exhausted (HTTP {resp.status_code})."
                    )
                self.logger.error("Google CSE HTTP %s: %s", resp.status_code, resp.text[:200])
                break
            if resp.status_code != 200:
                self.logger.error("Google CSE HTTP %s: %s", resp.status_code, resp.text[:200])
                break

            items = resp.json().get("items", []) or []
            for item in items:
                results.append(self._result(item.get("link", ""), item.get("snippet", ""), query))
            if len(items) < 10:  # FR-02: stop early when a page is short
                break
        return results[:max_results]


class SerpAPIProvider(SearchProvider):
    """SerpAPI Google engine (fallback)."""

    name = "serpapi"
    BASE_URL = "https://serpapi.com/search"

    async def search(self, query: str, *, max_results: int = 100) -> list[dict]:
        results: list[dict] = []
        start = 0
        while len(results) < max_results:
            params = {
                "api_key": self.cfg.serpapi_api_key,
                "engine": "google",
                "q": query,
                "num": 10,
                "start": start,
                "gl": "in",
            }
            resp = await request_with_retries(
                self.client, "GET", self.BASE_URL, logger=self.logger, params=params,
                timeout=10.0,
            )
            if resp is None or resp.status_code != 200:
                if resp is not None and resp.status_code == 429:
                    raise QuotaExhaustedError("SerpAPI quota exhausted (HTTP 429).")
                if resp is not None:
                    self.logger.error("SerpAPI HTTP %s: %s", resp.status_code, resp.text[:200])
                break
            organic = resp.json().get("organic_results", []) or []
            for item in organic:
                results.append(self._result(item.get("link", ""), item.get("snippet", ""), query))
            if len(organic) < 10:
                break
            start += 10
        return results[:max_results]


class BraveProvider(SearchProvider):
    """Brave Search API (fallback)."""

    name = "brave"
    BASE_URL = "https://api.search.brave.com/res/v1/web/search"

    async def search(self, query: str, *, max_results: int = 100) -> list[dict]:
        results: list[dict] = []
        offset = 0
        headers = {
            "X-Subscription-Token": self.cfg.brave_api_key or "",
            "Accept": "application/json",
        }
        while len(results) < max_results and offset <= 9:
            params = {"q": query, "count": 20, "offset": offset, "country": "IN"}
            resp = await request_with_retries(
                self.client, "GET", self.BASE_URL, logger=self.logger,
                params=params, headers=headers, timeout=10.0,
            )
            if resp is None or resp.status_code != 200:
                if resp is not None and resp.status_code == 429:
                    raise QuotaExhaustedError("Brave Search quota exhausted (HTTP 429).")
                if resp is not None:
                    self.logger.error("Brave HTTP %s: %s", resp.status_code, resp.text[:200])
                break
            web_results = (resp.json().get("web", {}) or {}).get("results", []) or []
            for item in web_results:
                results.append(
                    self._result(item.get("url", ""), item.get("description", ""), query)
                )
            if len(web_results) < 20:
                break
            offset += 1
        return results[:max_results]


_PROVIDERS = {
    "google_cse": GoogleCSEProvider,
    "serpapi": SerpAPIProvider,
    "brave": BraveProvider,
}


def get_provider(
    cfg: Config, client: httpx.AsyncClient, logger: logging.Logger, provider_name: str | None = None
) -> SearchProvider:
    """Instantiate the configured SearchProvider (FR-31/FR-32)."""
    name = provider_name or cfg.search_provider
    cls = _PROVIDERS.get(name)
    if cls is None:  # defensive; config.load_config already validates
        raise SearchProviderError(
            f"Unrecognised SEARCH_PROVIDER={name!r}. "
            f"Accepted: {', '.join(_PROVIDERS)}."
        )
    return cls(cfg, client, logger)
