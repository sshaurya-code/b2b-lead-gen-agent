"""Enrichment Module (Section 3c).

Enriches scraped company data via the Apollo.io REST API: organization enrich
(FR-08, always first) -> mixed_people search -> people bulk_match for named
contacts lacking an email. Filters to decision-maker seniorities (FR-09),
falls back to scraped data when Apollo returns nothing (FR-10), and halts the
Enrichment Module on an invalid API key (HTTP 401, Error Matrix).
"""

from __future__ import annotations

import logging

import httpx

from config import Config
from utils import request_with_retries

logger = logging.getLogger(__name__)

APOLLO_BASE = "https://api.apollo.io/api/v1"

# FR-09: only these seniorities are kept; everything else is discarded.
ALLOWED_SENIORITIES = {"c_suite", "owner", "founder", "head", "director", "manager"}

PERSON_TITLES = [
    "procurement", "purchasing", "sourcing", "supply chain", "sales",
    "business development", "import", "export", "director", "ceo",
    "founder", "managing director", "proprietor",
]

# Keyword -> company_type classification (Section 2.1, Table 2). First match wins;
# checked in priority order so specific types beat generic ones.
_TYPE_KEYWORDS: list[tuple[str, list[str]]] = [
    ("fabricator", ["fabricat", "window", "door frame", "fabrication"]),
    ("manufacturer", ["manufactur", "extrusion", "extruder", "industries", "mills"]),
    ("construction", ["construction", "civil contractor", "builder", "contractor"]),
    ("infrastructure", ["infrastructure", "infra ", "developers", "projects ltd"]),
    ("distributor", ["distributor", "wholesale", "distribution", "stockist"]),
    ("wholesaler", ["wholesaler", "bulk supply"]),
    ("retailer", ["retail", "hardware store", "hardware shop", "traders", "enterprises"]),
]


def classify_company_type(name: str | None, raw_text: str, industry: str | None) -> str:
    """Infer company_type from name/scraped text/Apollo industry (keyword-based)."""
    haystack = " ".join(filter(None, [name, raw_text[:2000], industry])).lower()
    for ctype, keywords in _TYPE_KEYWORDS:
        if any(kw in haystack for kw in keywords):
            return ctype
    return "unknown"


def _map_seniority(value: str | None) -> str:
    v = (value or "").lower()
    return v if v in ALLOWED_SENIORITIES else "other"


class ApolloKeyInvalidError(Exception):
    """Raised on HTTP 401 — halts the Enrichment Module for the entire run."""


class Enricher:
    """Apollo.io enrichment client."""

    def __init__(self, cfg: Config, client: httpx.AsyncClient):
        self.cfg = cfg
        self.client = client
        self.headers = {
            "x-api-key": cfg.apollo_api_key or "",
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
        }
        self.available = True  # flips to False after a 401 (Error Matrix)

    async def _post(self, path: str, payload: dict) -> dict | None:
        if not self.available:
            return None
        resp = await request_with_retries(
            self.client, "POST", f"{APOLLO_BASE}{path}",
            logger=logger, json=payload, headers=self.headers, timeout=10.0,
        )
        if resp is None:
            return None
        if resp.status_code == 401:
            self.available = False
            raise ApolloKeyInvalidError(
                "Apollo API key invalid — check APOLLO_API_KEY in .env"
            )
        if resp.status_code == 429:
            logger.error("Apollo rate limit persisted (429) for %s — skipping.", path)
            return None
        if resp.status_code != 200:
            logger.error("Apollo HTTP %s on %s: %s", resp.status_code, path, resp.text[:200])
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    async def enrich_one(self, scraped: dict) -> dict:
        """Enrich a single scraped record. Returns assembled enrichment fields."""
        domain = scraped["domain"]
        result = {
            "company_name": scraped.get("company_name"),
            "company_size": {"headcount_range": None, "revenue_band": None},
            "location": {"city": None, "state": None, "country": "India"},
            "industry": None,
            "contacts": [],
            "enriched_at": None,
            "enrichment_available": True,
        }

        if not self.available:
            result["enrichment_available"] = False
            result["contacts"] = _contacts_from_scraped(scraped)
            return result

        from datetime import datetime, timezone

        # FR-08: organization enrich first; store firmographics regardless of people.
        org = await self._post("/organizations/enrich", {"domain": domain})
        if org:
            organization = org.get("organization") or {}
            if organization.get("name"):
                result["company_name"] = organization["name"]
            result["company_size"] = {
                "headcount_range": _as_str(organization.get("estimated_num_employees")),
                "revenue_band": organization.get("annual_revenue_printed"),
            }
            result["location"] = {
                "city": organization.get("city"),
                "state": organization.get("state"),
                "country": "India",
            }
            result["industry"] = organization.get("industry")
            result["enriched_at"] = datetime.now(timezone.utc).isoformat()

        # mixed_people search for decision-makers.
        people_resp = await self._post(
            "/mixed_people/api_search",
            {
                "q_organization_domains": domain,
                "person_seniorities": sorted(ALLOWED_SENIORITIES),
                "person_titles": PERSON_TITLES,
                "reveal_personal_emails": True,
                "reveal_phone_number": True,
                "per_page": 10,
            },
        )
        apollo_contacts: list[dict] = []
        named_without_email: list[dict] = []
        if people_resp:
            for person in people_resp.get("people", []) or []:
                seniority = (person.get("seniority") or "").lower()
                if seniority not in ALLOWED_SENIORITIES:  # FR-09
                    continue
                contact = _contact_from_apollo(person)
                if contact["email"]:
                    apollo_contacts.append(contact)
                elif contact["name"]:
                    named_without_email.append({"name": contact["name"], "domain": domain, "_contact": contact})
            result["enriched_at"] = result["enriched_at"] or datetime.now(timezone.utc).isoformat()

        # bulk_match for named contacts lacking emails (up to 10 per call).
        if named_without_email:
            for batch_start in range(0, len(named_without_email), 10):
                batch = named_without_email[batch_start : batch_start + 10]
                bulk = await self._post(
                    "/people/bulk_match",
                    {"details": [{"name": b["name"], "domain": b["domain"]} for b in batch]},
                )
                matched = (bulk or {}).get("matches", []) or []
                for original, m in zip(batch, matched):
                    if m and m.get("email"):
                        apollo_contacts.append(_contact_from_apollo(m))
                    else:
                        apollo_contacts.append(original["_contact"])  # keep name-only contact

        # Fallback / merge (FR-10 + Section 3c merge rules).
        if not apollo_contacts:
            result["contacts"] = _contacts_from_scraped(scraped)
        else:
            result["contacts"] = _merge_contacts(apollo_contacts, scraped)

        return result

    async def enrich_all(self, scraped_records: list[dict]) -> list[dict]:
        """Enrich every scraped record; tolerate per-record failure."""
        out: list[dict] = []
        for rec in scraped_records:
            try:
                out.append(await self.enrich_one(rec))
            except ApolloKeyInvalidError as exc:
                logger.critical("%s Halting Enrichment Module for the rest of the run.", exc)
                # Remaining records (incl. this one) proceed scraping-only.
                fallback = {
                    "company_name": rec.get("company_name"),
                    "company_size": {"headcount_range": None, "revenue_band": None},
                    "location": {"city": None, "state": None, "country": "India"},
                    "industry": None,
                    "contacts": _contacts_from_scraped(rec),
                    "enriched_at": None,
                    "enrichment_available": False,
                }
                out.append(fallback)
        return out


def _as_str(value) -> str | None:
    return None if value is None else str(value)


def _contact_from_apollo(person: dict) -> dict:
    first = person.get("first_name") or ""
    last = person.get("last_name") or ""
    name = (f"{first} {last}").strip() or person.get("name")
    phone = None
    numbers = person.get("phone_numbers") or []
    if numbers and isinstance(numbers, list):
        phone = (numbers[0] or {}).get("sanitized_number")
    return {
        "name": name,
        "title": person.get("title"),
        "seniority": _map_seniority(person.get("seniority")),
        "email": person.get("email"),
        "phone": phone,
        "linkedin_url": person.get("linkedin_url"),
        "source": "apollo",
    }


def _contacts_from_scraped(scraped: dict) -> list[dict]:
    """Build scraped-only contacts (FR-10)."""
    emails = scraped.get("emails") or []
    phones = scraped.get("phones") or []
    contacts: list[dict] = []
    for i, email in enumerate(emails):
        contacts.append({
            "name": None,
            "title": None,
            "seniority": "unknown",
            "email": email,
            "phone": phones[i] if i < len(phones) else (phones[0] if phones and i == 0 else None),
            "linkedin_url": scraped.get("linkedin_url"),
            "source": "scraped",
        })
    if not contacts and phones:
        contacts.append({
            "name": None, "title": None, "seniority": "unknown",
            "email": None, "phone": phones[0],
            "linkedin_url": scraped.get("linkedin_url"), "source": "scraped",
        })
    return contacts


def _merge_contacts(apollo_contacts: list[dict], scraped: dict) -> list[dict]:
    """Merge Apollo + scraped contacts, dedup by email, set source (Section 3c)."""
    scraped_emails = {e.lower() for e in (scraped.get("emails") or [])}
    by_email: dict[str, dict] = {}
    extras: list[dict] = []

    for c in apollo_contacts:
        email = (c.get("email") or "").lower()
        if email and email in scraped_emails:
            c = {**c, "source": "both"}
        if email:
            by_email[email] = c
        else:
            extras.append(c)

    # Add scraped emails not already covered by Apollo.
    for email in scraped.get("emails") or []:
        if email.lower() not in by_email:
            by_email[email.lower()] = {
                "name": None, "title": None, "seniority": "unknown",
                "email": email, "phone": None,
                "linkedin_url": scraped.get("linkedin_url"), "source": "scraped",
            }
    return list(by_email.values()) + extras
