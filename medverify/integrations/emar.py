"""eMAR integration.

Strategy from the plan: do not build another eMAR. Plug into the one the home
already uses. Vendor APIs (Camascope / Person Centred Software, Access Group,
etc.) are partner-only, so each is an adapter implementing EmarAdapter once a
partnership is signed. Until then, homes can upload the administration export
their eMAR already produces (CSV), and homes on paper use our digital MAR.
"""

from __future__ import annotations

import csv
import io
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import to_utc_naive
from ..models import Home, MedicineItem
from ..rounds import AdministrationError, record_administration


@dataclass
class ExternalAdministration:
    external_id: str
    label_code: str
    ts: datetime
    outcome: str  # given | refused | spoilt
    carer_ref: str | None = None
    notes: str | None = None


class EmarAdapter(ABC):
    """Implement one of these per eMAR vendor."""

    source: str

    @abstractmethod
    def fetch_administrations(self, home: Home, since: datetime) -> list[ExternalAdministration]:
        ...


# Map the many words eMAR exports use onto our three outcomes.
OUTCOME_ALIASES = {
    "given": "given", "administered": "given", "taken": "given", "g": "given", "a": "given",
    "refused": "refused", "declined": "refused", "r": "refused",
    "spoilt": "spoilt", "spoiled": "spoilt", "destroyed": "spoilt", "wasted": "spoilt", "d": "spoilt",
}
REQUIRED = {"record_id", "label_code", "timestamp", "outcome"}


def parse_csv(text: str) -> tuple[list[ExternalAdministration], list[str]]:
    """Columns: record_id, label_code, timestamp (ISO 8601), outcome, carer (optional), notes (optional)."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    headers = {h.strip().lower() for h in (reader.fieldnames or [])}
    missing = REQUIRED - headers
    if missing:
        return [], [f"Missing columns: {', '.join(sorted(missing))}"]
    rows, errors = [], []
    for n, raw in enumerate(reader, start=2):
        row = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        outcome = OUTCOME_ALIASES.get(row["outcome"].lower())
        if outcome is None:
            errors.append(f"Line {n}: unknown outcome '{row['outcome']}'")
            continue
        try:
            ts = to_utc_naive(datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")))
        except ValueError:
            errors.append(f"Line {n}: bad timestamp '{row['timestamp']}'")
            continue
        rows.append(ExternalAdministration(row["record_id"], row["label_code"], ts, outcome,
                                           row.get("carer") or None, row.get("notes") or None))
    return rows, errors


def import_administrations(db: Session, home: Home, rows: list[ExternalAdministration], source: str) -> dict:
    imported, skipped, errors = 0, 0, []
    for r in rows:
        item = db.scalar(select(MedicineItem).where(MedicineItem.home_id == home.id,
                                                    MedicineItem.label_code == r.label_code))
        if item is None:
            errors.append(f"{r.external_id}: label {r.label_code} is not in the register")
            continue
        try:
            record_administration(db, item, r.outcome, ts=r.ts, notes=r.notes, source=source,
                                  external_id=r.external_id, carer_ref=r.carer_ref)
            imported += 1
        except AdministrationError as exc:
            if "already been imported" in str(exc):
                skipped += 1
            else:
                # Import what the eMAR says happened even if we would have blocked it;
                # reconciliation and alerts are how we surface it.
                errors.append(f"{r.external_id}: {exc}")
    return {"imported": imported, "duplicates_skipped": skipped, "errors": errors}


class CsvExportAdapter(EmarAdapter):
    """Adapter for homes that drop their eMAR's CSV export into MedVerify."""

    source = "emar_import"

    def __init__(self, text: str):
        self.rows, self.errors = parse_csv(text)

    def fetch_administrations(self, home: Home, since: datetime) -> list[ExternalAdministration]:
        return [r for r in self.rows if r.ts >= since]
