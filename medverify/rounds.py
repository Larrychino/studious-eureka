"""Medicines round: recording administrations, what is due now, and the digital MAR chart."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit
from .db import local, local_to_utc, utcnow
from .models import Administration, Home, MedicineItem, Resident, User


class AdministrationError(ValueError):
    pass


def record_administration(db: Session, item: MedicineItem, outcome: str, *, user: User | None = None,
                          ts: datetime | None = None, notes: str | None = None, source: str = "app",
                          external_id: str | None = None, carer_ref: str | None = None,
                          today: date | None = None) -> Administration:
    if outcome not in ("given", "refused", "spoilt"):
        raise AdministrationError("Outcome must be given, refused or spoilt")
    ts = ts or utcnow()
    if outcome == "given":
        if item.status == "quarantined":
            raise AdministrationError("This item is quarantined. Do not give it; ask the senior on shift.")
        if item.status != "in_stock":
            raise AdministrationError(f"This item is {item.status.replace('_', ' ')} and cannot be given.")
        expiry = item.effective_expiry
        if expiry and expiry < (today or ts.date()):
            raise AdministrationError(f"This item expired on {expiry:%d %b %Y}. Do not give it.")
    if external_id and db.scalar(select(Administration).where(Administration.source == source,
                                                             Administration.external_id == external_id)):
        raise AdministrationError("This record has already been imported")
    adm = Administration(item_id=item.id, carer_id=user.id if user else None, carer_ref=carer_ref, ts=ts,
                         outcome=outcome, source=source, external_id=external_id, notes=notes)
    db.add(adm)
    db.flush()
    audit.record(db, f"administration.{outcome}", actor=user or f"{source}:{carer_ref or 'unknown'}",
                 home_id=item.home_id, entity_type="administration", entity_id=adm.id,
                 details={"item_id": item.id, "label_code": item.label_code, "medicine": item.medicine_name,
                          "resident_ref": item.resident.ref if item.resident else None, "time": ts,
                          "source": source, "notes": notes})
    return adm


def dose_times(item: MedicineItem) -> list[time]:
    out = []
    for t in (item.dose_times or "").split(","):
        t = t.strip()
        if t:
            hh, mm = t.split(":")
            out.append(time(int(hh), int(mm)))
    return out


def due_now(db: Session, home: Home, now: datetime | None = None, window_min: int = 60) -> list[dict]:
    """Doses due within +/- window of now that have not been recorded yet.

    Dose times on labels are the home's local wall-clock times.
    """
    now = now or utcnow()
    local_now = local(now, home.timezone)
    items = db.scalars(select(MedicineItem).where(MedicineItem.home_id == home.id,
                                                  MedicineItem.status.in_(("in_stock", "quarantined"))))
    out = []
    for item in items:
        for t in dose_times(item):
            for day_offset in (-1, 0, 1):  # doses either side of midnight
                due_local = datetime.combine(local_now.date() + timedelta(days=day_offset), t)
                due = local_to_utc(due_local, home.timezone)
                if abs((due - now).total_seconds()) > window_min * 60:
                    continue
                recorded = db.scalar(select(Administration).where(
                    Administration.item_id == item.id,
                    Administration.ts >= due - timedelta(minutes=window_min),
                    Administration.ts <= due + timedelta(minutes=window_min)).limit(1))
                out.append({
                    "item_id": item.id, "label_code": item.label_code, "medicine": item.medicine_name,
                    "strength": item.strength, "dose_instructions": item.dose_instructions,
                    "resident_ref": item.resident.ref if item.resident else None,
                    "resident_name": item.resident.display_name if item.resident else None,
                    "tray": item.tray.label if item.tray else None,
                    "due": due.isoformat(), "due_local": due_local.strftime("%H:%M"),
                    "status": item.status, "recorded": recorded.outcome if recorded else None,
                })
    return sorted(out, key=lambda r: (r["due"], r["resident_ref"] or ""))


def mar_chart(db: Session, home: Home, resident: Resident, start: date, days: int = 7) -> dict:
    """Digital MAR chart for one resident: rows are item x dose time, columns are local days."""
    tz = home.timezone
    items = list(db.scalars(select(MedicineItem).where(MedicineItem.home_id == home.id,
                                                       MedicineItem.resident_id == resident.id)))
    window_start = local_to_utc(datetime.combine(start, time()), tz)
    window_end = local_to_utc(datetime.combine(start + timedelta(days=days), time()), tz)
    admins = list(db.scalars(select(Administration).where(
        Administration.item_id.in_([i.id for i in items] or [-1]),
        Administration.ts >= window_start, Administration.ts < window_end)))
    rows = []
    for item in items:
        times = dose_times(item)
        for t in times or [None]:
            cells = []
            for d in range(days):
                day = start + timedelta(days=d)
                if t is None:
                    hits = [a for a in admins if a.item_id == item.id and local(a.ts, tz).date() == day]
                else:
                    # Every record is shown, in the row of the nearest dose time that day.
                    hits = [a for a in admins if a.item_id == item.id and local(a.ts, tz).date() == day
                            and _nearest(times, local(a.ts, tz)) == t]
                cells.append([{"outcome": a.outcome, "time": f"{local(a.ts, tz):%H:%M}", "id": a.id,
                               "source": a.source} for a in hits])
            rows.append({"item_id": item.id, "medicine": item.medicine_name, "strength": item.strength,
                         "dose_instructions": item.dose_instructions, "status": item.status,
                         "time": t.strftime("%H:%M") if t else "When needed", "cells": cells})
    return {"resident_ref": resident.ref, "resident_name": resident.display_name, "start": start.isoformat(),
            "days": [(start + timedelta(days=d)).isoformat() for d in range(days)], "rows": rows}


def _nearest(times: list[time], when: datetime) -> time:
    minutes = when.hour * 60 + when.minute
    return min(times, key=lambda t: abs(t.hour * 60 + t.minute - minutes))
