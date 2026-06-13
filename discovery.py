"""Discovery Module (Section 3a).

Finds candidate company domains via the configured SearchProvider, deduplicates
by normalised domain (FR-03/FR-04), enforces the per-run query cap (FR-01) and
the persistent daily query counter (FR-27, NFR-01), and emits the Google CSE
deprecation warning (FR-35).
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

from config import Config
from providers import QuotaExhaustedError, SearchProvider
from utils import directory_markers, extract_gstin, is_excluded_directory, normalize_domain

logger = logging.getLogger(__name__)

# Discovery query templates (Section 3a, Table 5).
QUERY_TEMPLATES: list[str] = [
    # Directory-scoped (indiamart/tradeindia/justdial used only as search
    # targets — FR-12/8.6; never scraped directly).
    '"aluminium distributor" India site:indiamart.com OR site:tradeindia.com',
    '"hardware supplier" India site:indiamart.com OR site:tradeindia.com',
    '"aluminium fabricator" India site:indiamart.com OR site:tradeindia.com',
    '"aluminium" OR "hardware" supplier India site:justdial.com',
    '"hardware store" OR "aluminium dealer" India site:justdial.com',
    # City-level
    '"aluminium distributor" Mumbai India contact',
    '"hardware wholesaler" Delhi India supplier',
    '"aluminium extrusion" Ahmedabad India manufacturer',
    '"aluminium profiles" Rajkot India',
    '"building hardware" Pune India distributor',
    '"aluminium fabrication" Chennai India',
    '"hardware supplier" Surat India',
    # Generic
    "aluminium trade India B2B supplier contact",
    "hardware distributor India bulk supply enquiry",
    "aluminium sheet supplier India procurement",
]

# Google CSE deprecation milestones (Section 3a / 8.5).
_DEPRECATION_WARN_AFTER = date(2026, 12, 1)
_SHUTDOWN_DATE = "2027-01-01"


class QueryCounter:
    """Persistent daily query counter (FR-27). Resets on a new calendar day."""

    def __init__(self, path: Path):
        self.path = path
        self.today = date.today().isoformat()
        self.count = 0
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        if data.get("date") == self.today:
            self.count = int(data.get("count", 0))
        # New calendar day -> counter stays at 0 (FR-27).

    def save(self) -> None:
        self.path.write_text(json.dumps({"date": self.today, "count": self.count}))

    def increment(self) -> None:
        self.count += 1
        self.save()


def _maybe_warn_deprecation(provider_name: str, run_date: date) -> None:
    """FR-35: warn on every run after 2026-12-01 when using google_cse."""
    if provider_name == "google_cse" and run_date >= _DEPRECATION_WARN_AFTER:
        logger.warning(
            "DEPRECATION: Google Custom Search JSON API shuts down on %s. "
            "Migrate to SerpAPI or Brave Search (see Section 8.5): set "
            "SEARCH_PROVIDER=serpapi or brave and add the matching API key. "
            "No code changes required.",
            _SHUTDOWN_DATE,
        )


async def discover(
    cfg: Config,
    provider: SearchProvider,
    counter: QueryCounter,
    run_dt: datetime,
) -> list[dict]:
    """Run discovery and return deduplicated candidate dicts.

    Each candidate: ``{domain, url, snippet, discovery_source}``.
    """
    _maybe_warn_deprecation(provider.name, run_dt.date())

    daily_limit = cfg.google_cse_daily_limit if provider.name == "google_cse" else None

    candidates: dict[str, dict] = {}
    queries = QUERY_TEMPLATES
    executed = 0

    for query in queries:
        if executed >= cfg.queries_per_run:  # FR-01
            logger.info(
                "QUERIES_PER_RUN=%d reached — skipping remaining %d queries.",
                cfg.queries_per_run, len(queries) - executed,
            )
            break

        # NFR-01: pause discovery when the daily free-tier limit is hit.
        if daily_limit is not None and counter.count >= daily_limit:
            logger.warning(
                "Daily query limit (%d) reached for %s. Pausing discovery and "
                "saving progress. Raise GOOGLE_CSE_DAILY_LIMIT for paid tiers.",
                daily_limit, counter.today,
            )
            break

        try:
            results = await provider.search(query, max_results=100)
        except QuotaExhaustedError as exc:
            # Error matrix: log WARNING, halt discovery for the day, keep progress.
            logger.warning("%s Halting discovery; saving progress to date.", exc)
            break

        counter.increment()  # FR-27 / NFR-01
        executed += 1
        logger.info("Query %d/%d returned %d results: %s",
                    executed, cfg.queries_per_run, len(results), query)

        for r in results:
            domain = normalize_domain(r["url"])  # FR-03
            if not domain:
                continue
            if is_excluded_directory(domain):  # FR-12 / 8.6
                logger.info("Skipping directory domain (search-only): %s", domain)
                continue
            if domain in candidates:  # FR-04: dedup, no downstream quota spend
                logger.debug("Duplicate domain skipped: %s", domain)
                continue
            # Harvest verified-tag / GSTIN from the Google snippet (compliant —
            # no direct directory scraping).
            verified, sources = directory_markers(r["snippet"])
            candidates[domain] = {
                "domain": domain,
                "url": r["url"],
                "snippet": r["snippet"],
                "discovery_source": r["query"],
                "indiamart_verified": verified,
                "directory_sources": sources,
                "gstin": extract_gstin(r["snippet"]),
            }

    logger.info("Discovery complete: %d unique candidate domains.", len(candidates))
    return list(candidates.values())
