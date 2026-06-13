"""Tests for Google Places result parsing (places.parse_place)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from places import parse_place  # noqa: E402

SAMPLE = {
    "id": "ChIJabc123",
    "displayName": {"text": "Shree Aluminium Extrusions"},
    "formattedAddress": "Plot 12, GIDC, Ahmedabad, Gujarat 382213, India",
    "location": {"latitude": 23.0225, "longitude": 72.5714},
    "internationalPhoneNumber": "+91 98123 45670",
    "websiteUri": "https://shreealuminium.example.com",
    "types": ["hardware_store", "store", "point_of_interest", "establishment"],
    "businessStatus": "OPERATIONAL",
    "rating": 4.4,
    "userRatingCount": 52,
    "googleMapsUri": "https://maps.google.com/?cid=123",
    "addressComponents": [
        {"longText": "Ahmedabad", "types": ["locality", "political"]},
        {"longText": "Gujarat", "types": ["administrative_area_level_1", "political"]},
        {"longText": "India", "shortText": "IN", "types": ["country", "political"]},
    ],
}


def test_parse_extracts_location():
    p = parse_place(SAMPLE)
    assert p["city"] == "Ahmedabad"
    assert p["state"] == "Gujarat"
    assert p["country"] == "India"
    assert p["latitude"] == 23.0225 and p["longitude"] == 72.5714
    assert "Ahmedabad" in p["formatted_address"]


def test_parse_extracts_business_details():
    p = parse_place(SAMPLE)
    assert p["phone"] == "+91 98123 45670"
    assert p["website"] == "https://shreealuminium.example.com"
    assert p["rating"] == 4.4 and p["user_rating_count"] == 52
    assert p["business_status"] == "OPERATIONAL"
    assert p["maps_url"] == "https://maps.google.com/?cid=123"
    assert p["place_id"] == "ChIJabc123"


def test_parse_maps_company_type_hint():
    assert parse_place(SAMPLE)["company_type_hint"] == "retailer"  # hardware_store -> retailer


def test_parse_handles_missing_fields():
    p = parse_place({"id": "x", "types": []})
    assert p["city"] is None and p["state"] is None
    assert p["country"] == "India"
    assert p["company_type_hint"] == "unknown"
    assert p["phone"] is None
