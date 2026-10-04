"""Fridge temperature ingestion, excursion tracking and door/sensor watchdogs."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import alerts, audit
from .db import local, utcnow
from .decision_engine import assess_excursion
from .models import Excursion, Fridge, Home, TemperatureReading

FREEZING_C = 0.0


def open_excursion(db: Session, fridge: Fridge) -> Excursion | None:
    return db.scalar(select(Excursion).where(Excursion.fridge_id == fridge.id, Excursion.ended_at.is_(None)))


def _alert_out_of_range(db: Session, fridge: Fridge, exc: Excursion, temp_c: float) -> None:
    exc.alerted = True
    direction = "too cold" if temp_c < fridge.min_c else "too warm"
    alerts.raise_alert(
        db, fridge.home_id, "fridge_out_of_range",
        f"{fridge.name} is {direction}: {temp_c:.1f}°C",
        f"Safe range is {fridge.min_c:.0f}–{fridge.max_c:.0f}°C. Check the door is shut and the fridge has power. "
        "Do not use medicines from this fridge until MedVerify gives item-by-item instructions.",
        severity="critical",
        context={"fridge_id": fridge.id, "excursion_id": exc.id},
        dedupe_key=f"excursion:{exc.id}",
    )


def ingest_reading(db: Session, fridge: Fridge, ts: datetime, temp_c: float, door_open: bool | None = None,
                   source: str = "sensor") -> TemperatureReading:
    reading = TemperatureReading(fridge_id=fridge.id, ts=ts, temp_c=temp_c, door_open=door_open, source=source)
    db.add(reading)
    db.flush()
    home: Home = fridge.home

    # Late (back-filled) readings are stored and used when exposure is computed,
    # but do not drive the live state machine.
    if fridge.last_reading_at and ts < fridge.last_reading_at:
        if (temp_c < fridge.min_c or temp_c > fridge.max_c) and not _covered_by_excursion(db, fridge, ts):
            # Late data showing a problem we never saw live: a person must review it.
            alerts.raise_alert(
                db, home.id, "fridge_out_of_range",
                f"{fridge.name}: late data shows {temp_c:.1f}°C at {local(ts, home.timezone):%H:%M}",
                "The sensor sent this reading late (it may have been offline). Check whether medicines in this "
                "fridge were affected and ask the pharmacist if unsure.",
                severity="high", context={"fridge_id": fridge.id, "late_reading_id": reading.id},
                dedupe_key=f"late:{fridge.id}:{ts:%Y%m%d%H}")
        return reading
    fridge.last_temp_c, fridge.last_reading_at = temp_c, ts
    auto_resolve_offline(db, fridge)

    if door_open is not None:
        was_open = fridge.door_open_since is not None
        if door_open and not was_open:
            fridge.door_open_since = ts
        elif not door_open:
            fridge.door_open_since = None
            if was_open:
                _resolve_door_alerts(db, fridge, ts, home)
        if fridge.door_open_since and ts - fridge.door_open_since >= timedelta(minutes=home.door_open_alert_min):
            _alert_door(db, fridge, home)

    out_of_range = temp_c < fridge.min_c or temp_c > fridge.max_c
    exc = open_excursion(db, fridge)
    if out_of_range:
        if exc is None:
            exc = Excursion(fridge_id=fridge.id, started_at=ts, min_c=temp_c, max_c=temp_c)
            db.add(exc)
            db.flush()
            audit.record(db, "excursion.started", home_id=home.id, entity_type="excursion", entity_id=exc.id,
                         details={"fridge": fridge.name, "temp_c": temp_c}, ts=ts)
        exc.min_c, exc.max_c = min(exc.min_c, temp_c), max(exc.max_c, temp_c)
        long_enough = ts - exc.started_at >= timedelta(minutes=home.excursion_grace_min)
        if not exc.alerted and (temp_c <= FREEZING_C or long_enough):
            _alert_out_of_range(db, fridge, exc, temp_c)
    elif exc is not None:
        exc.ended_at = ts
        audit.record(db, "excursion.ended", home_id=home.id, entity_type="excursion", entity_id=exc.id,
                     details={"fridge": fridge.name, "temp_c": temp_c}, ts=ts)
        assess_excursion(db, exc)
        for a in alerts_for_excursion(db, exc):
            a.message += f" Back in range at {local(ts, home.timezone):%H:%M} ({temp_c:.1f}°C)."
    return reading


def _covered_by_excursion(db: Session, fridge: Fridge, ts: datetime) -> bool:
    return db.scalar(select(Excursion.id).where(
        Excursion.fridge_id == fridge.id, Excursion.started_at <= ts,
        (Excursion.ended_at.is_(None)) | (Excursion.ended_at >= ts)).limit(1)) is not None


def _alert_door(db: Session, fridge: Fridge, home: Home) -> None:
    alerts.raise_alert(db, home.id, "door_open", f"{fridge.name} door open",
                       f"The door has been open since {local(fridge.door_open_since, home.timezone):%H:%M}. "
                       "Please close it.", severity="medium", context={"fridge_id": fridge.id},
                       dedupe_key=f"door:{fridge.id}:{fridge.door_open_since.isoformat()}")


def _resolve_door_alerts(db: Session, fridge: Fridge, ts: datetime, home: Home) -> None:
    from .models import Alert

    for a in db.scalars(select(Alert).where(Alert.type == "door_open", Alert.home_id == home.id,
                                            Alert.resolved_at.is_(None))):
        if (a.context or {}).get("fridge_id") == fridge.id:
            a.resolved_at = ts
            a.action_taken = f"Door closed at {local(ts, home.timezone):%H:%M}"
            audit.record(db, "alert.auto_resolved", home_id=home.id, entity_type="alert", entity_id=a.id)


def alerts_for_excursion(db: Session, exc: Excursion):
    from .models import Alert

    return list(db.scalars(select(Alert).where(Alert.dedupe_key == f"excursion:{exc.id}")))


def watchdog(db: Session, now: datetime | None = None) -> None:
    """Periodic checks: sensors gone quiet, excursions past their grace period, doors left open."""
    now = now or utcnow()
    for fridge in db.scalars(select(Fridge)):
        home = fridge.home
        if fridge.sensor_device_id is not None:
            silent_for = None if fridge.last_reading_at is None else now - fridge.last_reading_at
            if silent_for is None or silent_for > timedelta(minutes=home.sensor_offline_min):
                since = f"{local(fridge.last_reading_at, home.timezone):%H:%M %d %b}" if fridge.last_reading_at else "never"
                alerts.raise_alert(db, home.id, "sensor_offline", f"{fridge.name} sensor is not reporting",
                                   f"Last reading: {since}. Record the temperature by hand and check the sensor battery and Wi-Fi.",
                                   severity="high", context={"fridge_id": fridge.id},
                                   dedupe_key=f"offline:{fridge.id}")
        exc = open_excursion(db, fridge)
        if exc and not exc.alerted and now - exc.started_at >= timedelta(minutes=home.excursion_grace_min):
            _alert_out_of_range(db, fridge, exc, fridge.last_temp_c if fridge.last_temp_c is not None else exc.max_c)
        if fridge.door_open_since and now - fridge.door_open_since >= timedelta(minutes=home.door_open_alert_min):
            _alert_door(db, fridge, home)


def auto_resolve_offline(db: Session, fridge: Fridge) -> None:
    from .models import Alert

    for a in db.scalars(select(Alert).where(Alert.dedupe_key == f"offline:{fridge.id}", Alert.resolved_at.is_(None))):
        a.resolved_at = utcnow()
        a.action_taken = "Sensor reporting again"
        audit.record(db, "alert.auto_resolved", home_id=a.home_id, entity_type="alert", entity_id=a.id)
