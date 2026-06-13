"""Tests for the lead scoring engine (Section 3e, Table 8)."""

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scorer import compute_qual_score, compute_score  # noqa: E402

RUN = datetime(2026, 6, 13, tzinfo=timezone.utc)


def iso(days_ago):
    return (RUN - timedelta(days=days_ago)).isoformat()


def test_empty_lead_scores_zero():
    score, bd = compute_score(None, [], RUN)
    assert score == 0
    assert bd == {"signal_recency": 0, "signal_count": 0, "contact_completeness": 0, "persona_seniority": 0}


def test_signal_recency_buckets():
    assert compute_score(None, [{"signal_date": iso(10)}], RUN)[1]["signal_recency"] == 40
    assert compute_score(None, [{"signal_date": iso(60)}], RUN)[1]["signal_recency"] == 25
    assert compute_score(None, [{"signal_date": iso(120)}], RUN)[1]["signal_recency"] == 10
    assert compute_score(None, [{"signal_date": iso(400)}], RUN)[1]["signal_recency"] == 0
    assert compute_score(None, [{"signal_date": None}], RUN)[1]["signal_recency"] == 0


def test_signal_recency_uses_most_recent():
    signals = [{"signal_date": iso(400)}, {"signal_date": iso(5)}]
    assert compute_score(None, signals, RUN)[1]["signal_recency"] == 40


def test_signal_count_capped_at_20():
    six = [{"signal_date": None}] * 6  # 5 beyond first * 5 = 25 -> capped 20
    assert compute_score(None, six, RUN)[1]["signal_count"] == 20
    three = [{"signal_date": None}] * 3  # 2 * 5 = 10
    assert compute_score(None, three, RUN)[1]["signal_count"] == 10
    one = [{"signal_date": None}]
    assert compute_score(None, one, RUN)[1]["signal_count"] == 0


def test_contact_completeness():
    full = {"email": "a@b.com", "phone": "999", "linkedin_url": "x", "seniority": "manager"}
    assert compute_score(full, [], RUN)[1]["contact_completeness"] == 30
    email_only = {"email": "a@b.com", "seniority": "manager"}
    assert compute_score(email_only, [], RUN)[1]["contact_completeness"] == 15
    phone_only = {"phone": "999", "seniority": "manager"}
    assert compute_score(phone_only, [], RUN)[1]["contact_completeness"] == 10


def test_persona_seniority_buckets():
    assert compute_score({"seniority": "c_suite"}, [], RUN)[1]["persona_seniority"] == 10
    assert compute_score({"seniority": "owner"}, [], RUN)[1]["persona_seniority"] == 10
    assert compute_score({"seniority": "founder"}, [], RUN)[1]["persona_seniority"] == 10
    assert compute_score({"seniority": "director"}, [], RUN)[1]["persona_seniority"] == 7
    assert compute_score({"seniority": "head"}, [], RUN)[1]["persona_seniority"] == 7
    assert compute_score({"seniority": "manager"}, [], RUN)[1]["persona_seniority"] == 4
    assert compute_score({"seniority": "other"}, [], RUN)[1]["persona_seniority"] == 0
    assert compute_score({"seniority": "unknown"}, [], RUN)[1]["persona_seniority"] == 0


def test_score_capped_at_100():
    contact = {"email": "a@b.com", "phone": "9", "linkedin_url": "x", "seniority": "c_suite"}
    signals = [{"signal_date": iso(1)}] + [{"signal_date": None}] * 10
    score, _ = compute_score(contact, signals, RUN)  # 40+20+30+10 = 100
    assert score == 100


def test_future_dated_signal_treated_as_recent():
    future = [{"signal_date": (RUN + timedelta(days=5)).isoformat()}]
    assert compute_score(None, future, RUN)[1]["signal_recency"] == 40


# --- qualification score (0-7) ---

def test_qual_score_all_criteria():
    total, bd = compute_qual_score(
        has_website=True, indiamart_verified=True, recent_activity=True,
        gst_verified=True, has_mobile=True,
    )
    assert total == 7
    assert bd == {"has_website": 1, "indiamart_verified": 1, "recent_activity": 2,
                  "gst_verified": 2, "has_mobile": 1}


def test_qual_score_none():
    total, bd = compute_qual_score(
        has_website=False, indiamart_verified=False, recent_activity=False,
        gst_verified=False, has_mobile=False,
    )
    assert total == 0 and all(v == 0 for v in bd.values())


def test_qual_score_weights():
    # recent activity and GST are worth 2 each
    assert compute_qual_score(has_website=False, indiamart_verified=False,
                              recent_activity=True, gst_verified=False, has_mobile=False)[0] == 2
    assert compute_qual_score(has_website=True, indiamart_verified=True,
                              recent_activity=False, gst_verified=False, has_mobile=True)[0] == 3
