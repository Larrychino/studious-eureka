"""Hash-chained audit log.

Each entry stores the hash of the previous entry, so any edit or deletion of a
past entry breaks the chain and is detected by verify_chain(). The log is what
a manager exports for a CQC inspection.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .db import utcnow
from .models import AuditEntry, User
from .security import sha256

GENESIS = "0" * 64


def _actor_label(actor: User | str | None) -> str:
    if actor is None:
        return "system"
    if isinstance(actor, str):
        return actor
    return f"{actor.display_name} ({actor.role}, user {actor.id})"


def _entry_hash(prev_hash: str, ts: datetime, actor: str, action: str, entity_type: str | None,
                entity_id: int | None, home_id: int | None, details: Any) -> str:
    payload = json.dumps(
        {
            "prev": prev_hash,
            "ts": ts.isoformat(timespec="microseconds"),
            "actor": actor,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "home_id": home_id,
            "details": details,
        },
        sort_keys=True,
        default=str,
    )
    return sha256(payload)


def record(db: Session, action: str, *, actor: User | str | None = None, home_id: int | None = None,
           entity_type: str | None = None, entity_id: int | None = None,
           details: dict[str, Any] | None = None, ts: datetime | None = None) -> AuditEntry:
    """Append an audit entry. The caller commits."""
    db.flush()
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        # Serialise writers so the hash chain stays linear under concurrency.
        db.execute(text("SELECT pg_advisory_xact_lock(7391)"))
    last = db.scalar(select(AuditEntry).order_by(AuditEntry.id.desc()).limit(1))
    prev_hash = last.hash if last else GENESIS
    ts = ts or utcnow()
    actor_label = _actor_label(actor)
    clean_details = json.loads(json.dumps(details, default=str)) if details else None
    entry = AuditEntry(
        home_id=home_id,
        ts=ts,
        actor=actor_label,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=clean_details,
        prev_hash=prev_hash,
        hash=_entry_hash(prev_hash, ts, actor_label, action, entity_type, entity_id, home_id, clean_details),
    )
    db.add(entry)
    db.flush()
    return entry


def verify_chain(db: Session) -> tuple[bool, int | None]:
    """Return (ok, first_bad_entry_id)."""
    prev = GENESIS
    for entry in db.scalars(select(AuditEntry).order_by(AuditEntry.id)):
        expected = _entry_hash(prev, entry.ts, entry.actor, entry.action, entry.entity_type,
                               entry.entity_id, entry.home_id, entry.details)
        if entry.prev_hash != prev or entry.hash != expected:
            return False, entry.id
        prev = entry.hash
    return True, None


def export_csv(db: Session, home_id: int, start: datetime | None = None, end: datetime | None = None) -> str:
    query = select(AuditEntry).where(AuditEntry.home_id == home_id).order_by(AuditEntry.id)
    if start:
        query = query.where(AuditEntry.ts >= start)
    if end:
        query = query.where(AuditEntry.ts <= end)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["entry_id", "time_utc", "actor", "action", "record_type", "record_id", "details", "hash"])
    for e in db.scalars(query):
        writer.writerow([
            e.id,
            e.ts.isoformat(timespec="seconds"),
            e.actor,
            e.action,
            e.entity_type or "",
            e.entity_id or "",
            json.dumps(e.details, sort_keys=True) if e.details else "",
            e.hash,
        ])
    return out.getvalue()
