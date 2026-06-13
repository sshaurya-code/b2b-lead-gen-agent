"""Entry point: runs the full Discovery -> Dashboard pipeline (Section 3).

Usage:
    python main.py

Completes discovery, scraping, enrichment, signal detection, scoring, storage,
and dashboard-data export. No error condition aborts the whole run (Section 6) —
each item is processed best-effort and failures are logged.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone

import httpx

from config import ConfigError, load_config
from discovery import QueryCounter, discover
from enricher import Enricher, classify_company_type
from models import SENIORITY_RANK
from providers import ProviderDeprecatedError, get_provider
from scorer import compute_score
from scraper import Scraper
from signals import SignalDetector
from storage import LeadStore
from utils import setup_logging


def _sort_contacts(contacts: list[dict]) -> list[dict]:
    return sorted(contacts, key=lambda c: SENIORITY_RANK.get(c.get("seniority", "unknown"), 0), reverse=True)


def _resolve_provider(cfg, client, logger):
    """Return a provider, switching off a deprecated google_cse if a fallback key exists."""
    try:
        return get_provider(cfg, client, logger)
    except ProviderDeprecatedError:
        pass
    if cfg.serpapi_api_key:
        logger.critical("Google CSE deprecated — switching to SerpAPI fallback (Section 8.5).")
        return get_provider(cfg, client, logger, provider_name="serpapi")
    if cfg.brave_api_key:
        logger.critical("Google CSE deprecated — switching to Brave fallback (Section 8.5).")
        return get_provider(cfg, client, logger, provider_name="brave")
    logger.critical(
        "Google CSE deprecated and no fallback configured. Register for SerpAPI "
        "(https://serpapi.com) or Brave (https://api.search.brave.com/app/keys), "
        "set SEARCH_PROVIDER + key in .env (Section 8.5)."
    )
    sys.exit(1)


async def run() -> None:
    cfg = load_config()
    logger = setup_logging(cfg.output_dir)
    run_dt = datetime.now(timezone.utc)
    logger.info("=== Lead generation run started %s (provider=%s, backend=%s) ===",
                run_dt.isoformat(), cfg.search_provider, cfg.storage_backend)

    store = LeadStore(cfg)
    counter = QueryCounter(cfg.output_dir / "query_counter.json")

    async with httpx.AsyncClient(follow_redirects=True) as client:
        provider = _resolve_provider(cfg, client, logger)

        # 1. Discovery
        try:
            candidates = await discover(cfg, provider, counter, run_dt)
        except ProviderDeprecatedError:
            provider = _resolve_provider(cfg, client, logger)
            candidates = await discover(cfg, provider, counter, run_dt)

        if not candidates:
            logger.warning("No candidate domains discovered. Exiting.")
            store.close()
            return

        # 2. Scraping
        async with Scraper(cfg) as scraper:
            scraped_records = await scraper.scrape_all(candidates)
        logger.info("Scraped %d/%d domains.", len(scraped_records), len(candidates))

        # 3. Enrichment
        enricher = Enricher(cfg, client)
        enriched_records = await enricher.enrich_all(scraped_records)

        # 4-6. Signals -> Scoring -> Storage
        detector = SignalDetector(cfg, provider, client)
        stored = 0
        for scraped, enrich in zip(scraped_records, enriched_records):
            signals = await detector.detect(
                enrich.get("company_name") or scraped.get("company_name"),
                scraped.get("raw_text", ""),
                run_dt,
            )
            contacts = _sort_contacts(enrich.get("contacts", []))
            best_contact = contacts[0] if contacts else None
            score, breakdown = compute_score(best_contact, signals, run_dt)

            company_name = (
                enrich.get("company_name") or scraped.get("company_name") or scraped["domain"]
            )
            lead = {
                "company_name": company_name,
                "company_type": classify_company_type(
                    company_name, scraped.get("raw_text", ""), enrich.get("industry")
                ),
                "website": scraped.get("website") or f"https://{scraped['domain']}",
                "location": enrich.get("location") or {"city": None, "state": None, "country": "India"},
                "company_size": enrich.get("company_size")
                or {"headcount_range": None, "revenue_band": None},
                "contacts": contacts,
                "buying_signals": signals,
                "lead_score": score,
                "score_breakdown": breakdown,
                "discovery_source": scraped.get("discovery_source", ""),
                "scraped_at": run_dt.isoformat(),
                "enriched_at": enrich.get("enriched_at"),
            }
            if store.save(lead):
                stored += 1

    # 7. Dashboard data export + summary
    export_path = store.export_dashboard_json()
    all_leads = store.all()
    with_contacts = sum(1 for l in all_leads if l.get("contacts"))
    with_signals = sum(1 for l in all_leads if l.get("buying_signals"))
    store.close()

    logger.info("=== Run complete ===")
    logger.info("Leads stored this run: %d | Total in store: %d", stored, len(all_leads))
    logger.info("With verified contacts: %d | With buying signals: %d", with_contacts, with_signals)
    logger.info("Dashboard data: %s", export_path)
    print(
        f"\nDone. {len(all_leads)} leads · {with_contacts} with contacts · "
        f"{with_signals} with signals.\nData written to {export_path}.\n"
        f"View: python serve.py  (then open http://localhost:8000)"
    )


def main() -> None:
    try:
        asyncio.run(run())
    except ConfigError as exc:
        print(f"Configuration error:\n{exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
