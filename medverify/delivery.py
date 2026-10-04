"""Delivery handover check: was the journey from pharmacy to home kept at 2-8 C?

The pharmacy packs fridge items in a reusable box with a temperature logger tag.
At the door the carer scans the box; the app reads the tag's journey record
and shows GREEN (accept) or RED (refuse, pharmacy notified).

A journey that cannot be verified (no readings, or gaps longer than the
logging interval allows) is RED: proof is the product.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import alerts, audit, notify
from .db import local, utcnow
from .models import Delivery, DeliveryBox, Pharmacy, ReplacementRequest, TemperatureReading, User

MIN_C, MAX_C = 2.0, 8.0
MAX_GAP_MIN = 20.0


@dataclass
class JourneyResult:
    ok: bool
    reason: str
    min_c: float | None
    max_c: float | None
    readings: int


def evaluate_journey(readings: list[tuple[datetime, float]], dispatched_at: datetime, arrived_at: datetime,
                     max_gap_min: float = MAX_GAP_MIN, tz: str | None = None) -> JourneyResult:
    pts = sorted((ts, t) for ts, t in readings if dispatched_at - timedelta(minutes=max_gap_min) <= ts <= arrived_at)
    if not pts:
        return JourneyResult(False, "No temperature record for this journey, so it cannot be verified.", None, None, 0)
    temps = [t for _, t in pts]
    lo, hi = min(temps), max(temps)
    if lo < MIN_C:
        return JourneyResult(False, f"Box got too cold during the journey ({lo:.1f}°C).", lo, hi, len(pts))
    if hi > MAX_C:
        return JourneyResult(False, f"Box got too warm during the journey ({hi:.1f}°C).", lo, hi, len(pts))
    edges = [dispatched_at] + [ts for ts, _ in pts] + [arrived_at]
    for a, b in zip(edges, edges[1:]):
        if (b - a).total_seconds() / 60.0 > max_gap_min:
            return JourneyResult(False, f"The temperature record has a gap from {local(a, tz):%H:%M} to {local(b, tz):%H:%M}, "
                                        "so the journey cannot be verified.", lo, hi, len(pts))
    return JourneyResult(True, f"Journey stayed within 2–8°C ({lo:.1f}–{hi:.1f}°C, {len(pts)} readings).",
                         lo, hi, len(pts))


def dispatch(db: Session, box: DeliveryBox, home_id: int, actor: User | str | None,
             dispatched_at: datetime | None = None, contents_note: str | None = None) -> Delivery:
    if db.scalar(select(Delivery).where(Delivery.box_id == box.id, Delivery.status == "in_transit")):
        raise ValueError("This box is already out on a delivery")
    d = Delivery(box_id=box.id, home_id=home_id, pharmacy_id=box.pharmacy_id,
                 dispatched_at=dispatched_at or utcnow(), contents_note=contents_note)
    db.add(d)
    db.flush()
    audit.record(db, "delivery.dispatched", actor=actor, home_id=home_id, entity_type="delivery", entity_id=d.id,
                 details={"box": box.code, "contents": contents_note})
    return d


def add_journey_readings(db: Session, delivery: Delivery, readings: list[tuple[datetime, float]]) -> int:
    existing = {r.ts for r in db.scalars(select(TemperatureReading).where(TemperatureReading.delivery_id == delivery.id))}
    added = 0
    for ts, temp in readings:
        if ts in existing:
            continue
        db.add(TemperatureReading(delivery_id=delivery.id, ts=ts, temp_c=temp, source="tag"))
        added += 1
    db.flush()
    return added


def check_at_door(db: Session, box_code: str, user: User, readings: list[tuple[datetime, float]] | None = None,
                  now: datetime | None = None) -> tuple[Delivery, JourneyResult]:
    now = now or utcnow()
    box = db.scalar(select(DeliveryBox).where(DeliveryBox.code == box_code))
    if box is None:
        raise LookupError("Unknown delivery box. Check the code on the box.")
    delivery = db.scalar(select(Delivery).where(Delivery.box_id == box.id, Delivery.status == "in_transit")
                         .order_by(Delivery.dispatched_at.desc()).limit(1))
    if delivery is None:
        raise LookupError("No delivery is expected in this box.")
    if delivery.home_id != user.home_id and user.role not in ("admin",):
        raise PermissionError("This delivery is addressed to a different home.")
    if readings:
        add_journey_readings(db, delivery, readings)
    stored = [(r.ts, r.temp_c) for r in db.scalars(select(TemperatureReading)
                                                   .where(TemperatureReading.delivery_id == delivery.id))]
    from .models import Home

    home = db.get(Home, delivery.home_id)
    result = evaluate_journey(stored, delivery.dispatched_at, now, tz=home.timezone)
    delivery.arrived_at, delivery.checked_by = now, user.id
    delivery.status = "accepted" if result.ok else "refused"
    delivery.result_reason = result.reason
    audit.record(db, f"delivery.{delivery.status}", actor=user, home_id=delivery.home_id, entity_type="delivery",
                 entity_id=delivery.id, details={"box": box.code, "reason": result.reason, "min_c": result.min_c,
                                                 "max_c": result.max_c, "readings": result.readings})
    if not result.ok:
        pharmacy = db.get(Pharmacy, delivery.pharmacy_id)
        db.add(ReplacementRequest(home_id=delivery.home_id, pharmacy_id=delivery.pharmacy_id, delivery_id=delivery.id,
                                  reason=f"Delivery refused at the door: {result.reason}"))
        alert = alerts.raise_alert(db, delivery.home_id, "delivery_refused", f"Delivery in box {box.code} refused",
                                   f"{result.reason} The pharmacy has been told and asked to send a replacement.",
                                   severity="high", context={"delivery_id": delivery.id},
                                   dedupe_key=f"delivery:{delivery.id}")
        text = (f"MedVerify: delivery in box {box.code} was REFUSED at {home.name} at {local(now, home.timezone):%H:%M}. "
                f"{result.reason} Please arrange a replacement.")
        if pharmacy and pharmacy.email:
            notify.queue(db, "email", pharmacy.email, text, alert.id)
        if pharmacy and pharmacy.phone:
            notify.queue(db, "sms", pharmacy.phone, text, alert.id)
    return delivery, result
