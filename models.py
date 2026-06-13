"""Canonical lead record schema (Section 3f, Table 9), validated with Pydantic v2.

FR-13: every lead is validated against this model before storage; invalid
records are logged at ERROR and skipped.

SPEC RECONCILIATION — contact seniority:
  Table 9 lists ``contacts[].seniority`` as ``c_suite | director | manager |
  other | unknown`` (5 values), but the contact-sorting ranking (Section 3c) and
  the scoring buckets (Table 8) reference ``owner``, ``founder`` and ``head`` as
  well. Apollo's people API also returns those values. Validating strictly
  against the 5-value list would drop legitimate Apollo contacts (owner/founder/
  head) at FR-13 and break seniority sorting/scoring. We therefore use the
  SUPERSET of all referenced values so sorting (Section 3c) and scoring
  (Table 8) remain faithful and no valid contact is discarded.
"""

from __future__ import annotations

import uuid
from enum import Enum

from pydantic import BaseModel, Field


class CompanyType(str, Enum):
    retailer = "retailer"
    distributor = "distributor"
    wholesaler = "wholesaler"
    manufacturer = "manufacturer"
    fabricator = "fabricator"
    construction = "construction"
    infrastructure = "infrastructure"
    unknown = "unknown"


class Seniority(str, Enum):
    c_suite = "c_suite"
    owner = "owner"
    founder = "founder"
    head = "head"
    director = "director"
    manager = "manager"
    other = "other"
    unknown = "unknown"


class ContactSource(str, Enum):
    apollo = "apollo"
    scraped = "scraped"
    both = "both"


class SignalType(str, Enum):
    website_keyword = "website_keyword"
    news_expansion = "news_expansion"


class Confidence(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class Location(BaseModel):
    city: str | None = None
    state: str | None = None
    country: str = "India"  # FR: hardcoded India


class CompanySize(BaseModel):
    headcount_range: str | None = None  # Apollo estimated_num_employees
    revenue_band: str | None = None  # Apollo annual_revenue_printed


class Contact(BaseModel):
    name: str | None = None
    title: str | None = None
    seniority: Seniority = Seniority.unknown
    email: str | None = None
    phone: str | None = None
    linkedin_url: str | None = None
    source: ContactSource


class BuyingSignal(BaseModel):
    type: SignalType
    text_snippet: str
    signal_date: str | None = None  # ISO8601 or null
    confidence: Confidence


class ScoreBreakdown(BaseModel):
    signal_recency: int = 0
    signal_count: int = 0
    contact_completeness: int = 0
    persona_seniority: int = 0


class Lead(BaseModel):
    lead_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    company_name: str
    company_type: CompanyType = CompanyType.unknown
    website: str
    location: Location = Field(default_factory=Location)
    company_size: CompanySize = Field(default_factory=CompanySize)
    contacts: list[Contact] = Field(default_factory=list)
    buying_signals: list[BuyingSignal] = Field(default_factory=list)
    lead_score: int = 0
    score_breakdown: ScoreBreakdown = Field(default_factory=ScoreBreakdown)
    discovery_source: str = ""
    scraped_at: str
    enriched_at: str | None = None


# Seniority ranking, highest to lowest (Section 3c). Used for contact sorting.
SENIORITY_RANK: dict[str, int] = {
    "c_suite": 7,
    "owner": 6,
    "founder": 5,
    "head": 4,
    "director": 3,
    "manager": 2,
    "other": 1,
    "unknown": 0,
}


def sort_contacts(contacts: list[Contact]) -> list[Contact]:
    """Sort contacts by seniority ranking, highest first (Section 3c)."""
    return sorted(
        contacts,
        key=lambda c: SENIORITY_RANK.get(c.seniority.value, 0),
        reverse=True,
    )
