"""Tests for shared utilities: domain normalisation (FR-03/14) and PII redaction (FR-33)."""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import (  # noqa: E402
    PIIRedactionFilter,
    directory_markers,
    extract_gstin,
    is_excluded_directory,
    is_indian_mobile,
    normalize_domain,
    redact_pii,
)


def test_normalize_domain_strips_everything():
    assert normalize_domain("https://www.Example.com/contact?x=1#frag") == "example.com"
    assert normalize_domain("http://EXAMPLE.com/") == "example.com"
    assert normalize_domain("example.com") == "example.com"
    assert normalize_domain("https://sub.example.co.in/path") == "sub.example.co.in"
    assert normalize_domain("https://example.com:8080/x") == "example.com"
    assert normalize_domain("") == ""


def test_normalize_domain_idempotent():
    once = normalize_domain("https://www.Acme-Hardware.com/about")
    assert normalize_domain(once) == once


def test_excluded_directories():
    assert is_excluded_directory("indiamart.com")
    assert is_excluded_directory("tradeindia.com")
    assert is_excluded_directory("dir.indiamart.com")
    assert not is_excluded_directory("example.com")


def test_redact_pii_email_and_phone():
    out = redact_pii("Reach me at john@acme.com or +919876543210 today")
    assert "[email redacted]" in out
    assert "[phone redacted]" in out
    assert "john@acme.com" not in out
    assert "9876543210" not in out


def test_pii_filter_masks_log_record():
    f = PIIRedactionFilter()
    rec = logging.LogRecord("t", logging.INFO, __file__, 1,
                            "contact %s", ("buyer@firm.in",), None)
    f.filter(rec)
    assert rec.args == ("[email redacted]",)


def test_extract_gstin():
    assert extract_gstin("Our GSTIN: 24ABCDE1234F1Z5 thanks") == "24ABCDE1234F1Z5"
    assert extract_gstin("gst 27aabcu9603r1zm here") == "27AABCU9603R1ZM"
    assert extract_gstin("no gst here") is None


def test_is_indian_mobile():
    assert is_indian_mobile("+91 98765 43210")
    assert is_indian_mobile("9876543210")
    assert is_indian_mobile("098765 43210")
    assert not is_indian_mobile("+91 33 2233 4455")  # landline (starts 3)
    assert not is_indian_mobile("12345")
    assert not is_indian_mobile(None)


def test_directory_markers():
    v, src = directory_markers("Find us on IndiaMART — TrustSEAL Verified exporter")
    assert v is True and "indiamart" in src
    v2, src2 = directory_markers("Listed on justdial.com")
    assert v2 is False and "justdial" in src2
    v3, src3 = directory_markers("just a normal site")
    assert v3 is False and src3 == []
