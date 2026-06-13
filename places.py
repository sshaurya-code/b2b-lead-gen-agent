"""Google Places enrichment — verified location & business details.

Uses the **Places API (New)** Text Search to resolve each company's location
(city / state / formatted address / geo coordinates) plus phone, website,
business type, rating, and Maps link. This complements Apollo.io: Apollo
supplies decision-maker contacts and firmographics; Google Places supplies
verified location and the surrounding business details.

Endpoint: POST https://places.googleapis.com/v1/places:searchText
Auth:     X-Goog-Api-Key header + a required X-Goog-FieldMask.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config import Config
from utils import request_with_retries

if TYPE_CHECKING:  # httpx imported lazily so _parse stays dependency-free for tests
    import httpx

logger = logging.getLogger(__name__)

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.addressComponents",
    "places.location",
    "places.internationalPhoneNumber",
    "places.nationalPhoneNumber",
    "places.websiteUri",
    "places.types",
    "places.businessStatus",
    "places.rating",
    "places.userRatingCount",
    "places.googleMapsUri",
])

# Google Places type -> our company_type. Only used to fill an "unknown" type.
_PLACES_TYPE_MAP = {
    "hardware_store": "retailer",
    "home_improvement_store": "retailer",
    "home_goods_store": "retailer",
    "general_contractor": "construction",
    "store": "retailer",
}


def _empty() -> dict:
    return {
        "city": None, "state": None, "country": None,
        "formatted_address": None, "latitude": None, "longitude": None,
        "phone": None, "website": None, "place_types": [],
        "company_type_hint": "unknown", "rating": None, "user_rating_count": None,
        "business_status": None, "maps_url": None, "place_id": None,
    }


def parse_place(p: dict) -> dict:
    """Map a Places API result object to our flat enrichment dict."""
    comps = p.get("addressComponents") or []

    def comp(*wanted: str) -> str | None:
        for want in wanted:
            for c in comps:
                if want in (c.get("types") or []):
                    return c.get("longText") or c.get("shortText")
        return None

    loc = p.get("location") or {}
    types = p.get("types") or []
    type_hint = "unknown"
    for t in types:
        if t in _PLACES_TYPE_MAP:
            type_hint = _PLACES_TYPE_MAP[t]
            break

    return {
        "city": comp("locality", "administrative_area_level_2", "postal_town"),
        "state": comp("administrative_area_level_1"),
        "country": comp("country") or "India",
        "formatted_address": p.get("formattedAddress"),
        "latitude": loc.get("latitude"),
        "longitude": loc.get("longitude"),
        "phone": p.get("internationalPhoneNumber") or p.get("nationalPhoneNumber"),
        "website": p.get("websiteUri"),
        "place_types": types,
        "company_type_hint": type_hint,
        "rating": p.get("rating"),
        "user_rating_count": p.get("userRatingCount"),
        "business_status": p.get("businessStatus"),
        "maps_url": p.get("googleMapsUri"),
        "place_id": p.get("id"),
    }


class PlacesClient:
    """Google Places (New) Text Search client."""

    def __init__(self, cfg: Config, client: "httpx.AsyncClient"):
        self.cfg = cfg
        self.client = client
        self.available = bool(cfg.google_places_api_key)
        self.headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": cfg.google_places_api_key or "",
            "X-Goog-FieldMask": FIELD_MASK,
        }
        if not self.available:
            logger.info("GOOGLE_PLACES_API_KEY not set — location enrichment via Places disabled.")

    async def enrich(self, company_name: str | None, hint_city: str | None = None) -> dict:
        """Resolve location + business details for a company. Best-effort."""
        if not self.available or not company_name:
            return _empty()
        query = company_name + (f" {hint_city}" if hint_city else "") + " India"
        body = {"textQuery": query, "regionCode": "IN", "maxResultCount": 1, "languageCode": "en"}
        resp = await request_with_retries(
            self.client, "POST", PLACES_URL, logger=logger,
            json=body, headers=self.headers, timeout=10.0,
        )
        if resp is None:
            return _empty()
        if resp.status_code in (401, 403):
            self.available = False
            logger.error(
                "Google Places API key invalid/forbidden (HTTP %s) — disabling Places "
                "enrichment for the rest of the run. Check GOOGLE_PLACES_API_KEY and that "
                "'Places API (New)' is enabled.", resp.status_code,
            )
            return _empty()
        if resp.status_code != 200:
            logger.error("Places HTTP %s: %s", resp.status_code, resp.text[:200])
            return _empty()
        places = resp.json().get("places") or []
        if not places:
            logger.debug("No Places result for %s", company_name)
            return _empty()
        return parse_place(places[0])
