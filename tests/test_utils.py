"""Tests for shared utilities: domain normalisation (FR-03/14) and PII redaction (FR-33)."""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import (  # noqa: E402
    PIIRedactionFilter,
    is_excluded_directory,
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
