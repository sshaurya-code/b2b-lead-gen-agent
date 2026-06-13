"""Deterministic 0-100 lead scoring engine (Section 3e, Table 8).

FR-18: implements all four dimensions exactly as specified.
FR-19: final score is capped at 100.

This module has NO third-party dependencies (pure stdlib) so it can be unit
tested in isolation. It operates on plain dicts rather than Pydantic models for
the same reason; the orchestrator passes the already seniority-sorted best
contact and the list of buying signals.
"""

from __future__ import annotations

from datetime import datetime, timezone

# Persona-seniority point buckets (Table 8). Kept local to preserve this
# module's zero-dependency contract.
_SENIORITY_POINTS = {
    "c_suite": 10,
    "owner": 10,
    "founder": 10,
    "head": 7,
    "director": 7,
    "manager": 4,
    "other": 0,
    "unknown": 0,
}


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _signal_recency_points(signals: list[dict], run_date: datetime) -> int:
    """+40 within 30d, +25 31-90d, +10 91-180d, +0 older/undated (max 40)."""
    dated = [d for d in (_parse_iso(s.get("signal_date")) for s in signals) if d]
    if not dated:
        return 0
    most_recent = max(dated)
    days = (run_date - most_recent).days
    if days < 0:  # future-dated signal — treat as most recent
        days = 0
    if days <= 30:
        return 40
    if days <= 90:
        return 25
    if days <= 180:
        return 10
    return 0


def _signal_count_points(signals: list[dict]) -> int:
    """+5 for each signal beyond the first, capped at 20."""
    extra = max(0, len(signals) - 1)
    return min(extra * 5, 20)


def _contact_completeness_points(best_contact: dict | None) -> int:
    """email +15, phone +10, linkedin +5 on the best contact (max 30)."""
    if not best_contact:
        return 0
    points = 0
    if best_contact.get("email"):
        points += 15
    if best_contact.get("phone"):
        points += 10
    if best_contact.get("linkedin_url"):
        points += 5
    return points


def _persona_seniority_points(best_contact: dict | None) -> int:
    """c_suite/owner/founder +10, director/head +7, manager +4, else 0 (max 10)."""
    if not best_contact:
        return 0
    seniority = (best_contact.get("seniority") or "unknown")
    if hasattr(seniority, "value"):  # tolerate enum input
        seniority = seniority.value
    return _SENIORITY_POINTS.get(seniority, 0)


def compute_score(
    best_contact: dict | None,
    signals: list[dict],
    run_date: datetime,
) -> tuple[int, dict]:
    """Return ``(lead_score, score_breakdown)``.

    ``best_contact`` is the highest-seniority contact (or None). ``signals`` is
    the list of buying-signal dicts (each may carry ``signal_date``).
    ``run_date`` is the timezone-aware run timestamp.
    """
    breakdown = {
        "signal_recency": _signal_recency_points(signals, run_date),
        "signal_count": _signal_count_points(signals),
        "contact_completeness": _contact_completeness_points(best_contact),
        "persona_seniority": _persona_seniority_points(best_contact),
    }
    total = min(sum(breakdown.values()), 100)  # FR-19: cap at 100
    return total, breakdown
