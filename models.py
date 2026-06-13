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
    places = "places"  # business phone resolved via Google Places (see places.py)


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
    formatted_address: str | None = None  # Google Places formatted address
    latitude: float | None = None
    longitude: float | None = None


class CompanySize(BaseModel):
    headcount_range: str | None = None  # Apollo estimated_num_employees
    revenue_band: str | None = None  # Apollo annual_revenue_printed


class Place(BaseModel):
    """Business details resolved via Google Places (location's companion data)."""

    rating: float | None = None
    user_rating_count: int | None = None
    business_status: str | None = None  # OPERATIONAL / CLOSED_TEMPORARILY / ...
    types: list[str] = []
    maps_url: str | None = None
    place_id: str | None = None


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
    source_url: str | None = None  # where the signal was found (website page or news result)
    source_query: str | None = None  # the search query that surfaced a news signal


class ScoreBreakdown(BaseModel):
    signal_recency: int = 0
    signal_count: int = 0
    contact_completeness: int = 0
    persona_seniority: int = 0


class Gst(BaseModel):
    gstin: str | None = None
    status: str | None = None  # e.g. "Active", "Cancelled"
    verified: bool = False  # confirmed active via GST verification API
    legal_name: str | None = None


class QualBreakdown(BaseModel):
    """Simple 0-7 qualification scorecard (points per criterion)."""

    has_website: int = 0          # +1
    indiamart_verified: int = 0   # +1
    recent_activity: int = 0      # +2
    gst_verified: int = 0         # +2
    has_mobile: int = 0           # +1


class Lead(BaseModel):
    lead_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    company_name: str
    company_type: CompanyType = CompanyType.unknown
    website: str
    location: Location = Field(default_factory=Location)
    company_size: CompanySize = Field(default_factory=CompanySize)
    place: Place | None = None
    contacts: list[Contact] = Field(default_factory=list)
    buying_signals: list[BuyingSignal] = Field(default_factory=list)
    gst: Gst | None = None
    indiamart_verified: bool = False
    directory_sources: list[str] = Field(default_factory=list)  # e.g. ["indiamart", "justdial"]
    lead_score: int = 0
    score_breakdown: ScoreBreakdown = Field(default_factory=ScoreBreakdown)
    qual_score: int = 0  # simple 0-7 qualification score (see scorer.compute_qual_score)
    qual_breakdown: QualBreakdown = Field(default_factory=QualBreakdown)
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
