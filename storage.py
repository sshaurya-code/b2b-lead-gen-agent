"""Data Layer (Section 3f).

Validates lead records against the Pydantic model (FR-13), persists to SQLite
(default) or a JSON flat file, and deduplicates by normalised domain with the
merge rules in FR-15. All files live in ``OUTPUT_DIR`` (FR-34).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from config import Config
from models import Lead
from utils import normalize_domain

logger = logging.getLogger(__name__)


def _merge_lead_dicts(existing: dict, incoming: dict) -> dict:
    """Merge two lead dicts for the same domain (FR-15)."""
    merged = dict(existing)

    # contacts[] — dedup by email (None-email contacts kept individually).
    seen_emails: set[str] = set()
    contacts: list[dict] = []
    for c in (existing.get("contacts", []) + incoming.get("contacts", [])):
        email = (c.get("email") or "").lower()
        if email:
            if email in seen_emails:
                continue
            seen_emails.add(email)
        contacts.append(c)
    merged["contacts"] = contacts

    # buying_signals[] — dedup by (type, text_snippet).
    seen_sig: set[tuple] = set()
    signals: list[dict] = []
    for s in (existing.get("buying_signals", []) + incoming.get("buying_signals", [])):
        key = (s.get("type"), s.get("text_snippet"))
        if key in seen_sig:
            continue
        seen_sig.add(key)
        signals.append(s)
    merged["buying_signals"] = signals

    # enriched_at -> most recent; lead_score -> higher of the two.
    merged["enriched_at"] = _max_iso(existing.get("enriched_at"), incoming.get("enriched_at"))
    merged["lead_score"] = max(existing.get("lead_score", 0), incoming.get("lead_score", 0))
    return merged


def _max_iso(a: str | None, b: str | None) -> str | None:
    candidates = [x for x in (a, b) if x]
    if not candidates:
        return None
    try:
        return max(candidates, key=lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")))
    except ValueError:
        return candidates[-1]


class LeadStore:
    """SQLite or JSON-backed lead store with validation and dedup."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.backend = cfg.storage_backend
        cfg.output_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = cfg.output_dir / "leads.db"
        self.json_path = cfg.output_dir / "leads.json"
        self._conn: sqlite3.Connection | None = None
        if self.backend == "sqlite":
            self._init_sqlite()

    # -- SQLite ------------------------------------------------------------ #

    def _init_sqlite(self) -> None:
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS leads (
                domain TEXT PRIMARY KEY,
                lead_id TEXT,
                lead_score INTEGER,
                company_type TEXT,
                city TEXT,
                scraped_at TEXT,
                data TEXT
            )
            """
        )
        for col in ("lead_id", "lead_score", "company_type", "city", "scraped_at"):
            self._conn.execute(f"CREATE INDEX IF NOT EXISTS idx_leads_{col} ON leads({col})")
        self._conn.commit()

    # -- public API -------------------------------------------------------- #

    def save(self, lead_data: dict) -> bool:
        """Validate (FR-13), dedup/merge (FR-15), and persist. Returns success."""
        try:
            validated = Lead.model_validate(lead_data).model_dump()
        except ValidationError as exc:
            logger.error("Lead failed validation, skipping (%s): %s",
                         lead_data.get("website", "?"), exc.errors())
            return False

        domain = normalize_domain(validated["website"])
        if not domain:
            logger.error("Lead has no resolvable domain, skipping: %s", validated.get("website"))
            return False

        existing = self._get(domain)
        if existing:
            validated = _merge_lead_dicts(existing, validated)
            logger.debug("Merged duplicate lead for domain %s", domain)
        self._put(domain, validated)
        return True

    def export_dashboard_json(self, path: Path | None = None) -> Path:
        """Write all leads as a JSON array for the dashboard to consume."""
        target = path or self.json_path
        target.write_text(json.dumps(self.all(), indent=2, ensure_ascii=False))
        return target

    def all(self) -> list[dict]:
        if self.backend == "sqlite":
            assert self._conn is not None
            rows = self._conn.execute("SELECT data FROM leads").fetchall()
            return [json.loads(r[0]) for r in rows]
        return self._load_json()

    def close(self) -> None:
        if self._conn:
            self._conn.commit()
            self._conn.close()

    # -- backend internals ------------------------------------------------- #

    def _get(self, domain: str) -> dict | None:
        if self.backend == "sqlite":
            assert self._conn is not None
            row = self._conn.execute("SELECT data FROM leads WHERE domain = ?", (domain,)).fetchone()
            return json.loads(row[0]) if row else None
        for rec in self._load_json():
            if normalize_domain(rec.get("website", "")) == domain:
                return rec
        return None

    def _put(self, domain: str, data: dict) -> None:
        if self.backend == "sqlite":
            assert self._conn is not None
            self._conn.execute(
                """
                INSERT INTO leads (domain, lead_id, lead_score, company_type, city, scraped_at, data)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(domain) DO UPDATE SET
                    lead_id=excluded.lead_id, lead_score=excluded.lead_score,
                    company_type=excluded.company_type, city=excluded.city,
                    scraped_at=excluded.scraped_at, data=excluded.data
                """,
                (
                    domain, data["lead_id"], data["lead_score"], data["company_type"],
                    (data.get("location") or {}).get("city"), data["scraped_at"],
                    json.dumps(data, ensure_ascii=False),
                ),
            )
            self._conn.commit()
        else:
            records = self._load_json()
            records = [r for r in records if normalize_domain(r.get("website", "")) != domain]
            records.append(data)
            self.json_path.write_text(json.dumps(records, indent=2, ensure_ascii=False))

    def _load_json(self) -> list[dict]:
        if not self.json_path.exists():
            return []
        try:
            return json.loads(self.json_path.read_text())
        except (json.JSONDecodeError, OSError):
            return []
