"""Raising alerts and routing them to the right people.

Mismatches go to the senior on shift (plan: "raises an alert to the senior on
shift and is logged"). Fridge problems go to everyone on shift. Delivery
refusals go to the pharmacy as well.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit, notify
from .db import utcnow
from .models import Alert, Home, User

# Which roles on shift receive each alert type.
ROUTING: dict[str, tuple[str, ...]] = {
    "stock_mismatch": ("senior", "manager"),
    "fridge_out_of_range": ("carer", "senior", "manager"),
    "excursion_decision": ("carer", "senior", "manager"),
    "sensor_offline": ("senior", "manager"),
    "door_open": ("carer", "senior"),
    "delivery_refused": ("senior", "manager"),
    "quarantine_review": ("senior", "manager"),
    "expiry": ("senior",),
}


def recipients(db: Session, home_id: int, alert_type: str) -> list[User]:
    roles = ROUTING.get(alert_type, ("senior", "manager"))
    users = list(db.scalars(select(User).where(User.home_id == home_id, User.active.is_(True), User.role.in_(roles))))
    on_shift = [u for u in users if u.on_shift]
    if on_shift:
        return on_shift
    # Nobody marked on shift: fall back to managers so the alert is never silent.
    return [u for u in users if u.role == "manager"] or users


def raise_alert(db: Session, home_id: int, alert_type: str, title: str, message: str, *,
                severity: str = "high", context: dict[str, Any] | None = None,
                dedupe_key: str | None = None, sms: bool = True) -> Alert:
    """Create an alert (or return the open one with the same dedupe key), notify and audit it."""
    if dedupe_key:
        existing = db.scalar(select(Alert).where(Alert.dedupe_key == dedupe_key, Alert.resolved_at.is_(None)))
        if existing is not None:
            return existing
    alert = Alert(home_id=home_id, type=alert_type, severity=severity, title=title, message=message,
                  context=context, dedupe_key=dedupe_key, raised_at=utcnow())
    db.add(alert)
    db.flush()
    home = db.get(Home, home_id)
    text = f"MedVerify {home.name if home else ''}: {title}. {message}"
    for user in recipients(db, home_id, alert_type):
        notify.queue(db, "app", f"user:{user.id}", text, alert.id)
        if sms and user.phone and severity in ("high", "critical"):
            notify.queue(db, "sms", user.phone, text, alert.id)
    audit.record(db, "alert.raised", home_id=home_id, entity_type="alert", entity_id=alert.id,
                 details={"type": alert_type, "severity": severity, "title": title, "context": context})
    return alert


def mark_seen(db: Session, alert: Alert, user: User) -> Alert:
    if alert.seen_at is None:
        alert.seen_at, alert.seen_by = utcnow(), user.id
        audit.record(db, "alert.seen", actor=user, home_id=alert.home_id, entity_type="alert", entity_id=alert.id)
    return alert


def resolve(db: Session, alert: Alert, user: User, action_taken: str) -> Alert:
    mark_seen(db, alert, user)
    alert.action_taken = action_taken
    alert.resolved_at, alert.resolved_by = utcnow(), user.id
    audit.record(db, "alert.resolved", actor=user, home_id=alert.home_id, entity_type="alert", entity_id=alert.id,
                 details={"action_taken": action_taken})
    return alert
