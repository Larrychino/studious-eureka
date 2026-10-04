"""Device ingestion: fridge sensors, weight trays and delivery tags.

Devices authenticate with X-Device-Id / X-Device-Key headers. Payloads are
batched so a device can back-fill readings after a Wi-Fi drop.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import to_utc_naive, utcnow
from ..delivery import add_journey_readings
from ..models import Delivery, DeliveryBox, Device, Fridge, Tray
from ..reconciliation import record_tray_event
from ..temperature import ingest_reading
from .deps import current_device, get_db

router = APIRouter(prefix="/api/devices")


class Reading(BaseModel):
    ts: datetime | None = None
    temp_c: float = Field(ge=-50, le=80)
    door_open: bool | None = None


class ReadingsIn(BaseModel):
    readings: list[Reading] = Field(min_length=1, max_length=2000)
    firmware: str | None = None


@router.post("/readings")
def post_readings(body: ReadingsIn, db: Session = Depends(get_db), device: Device = Depends(current_device)):
    if device.kind != "fridge_sensor":
        raise HTTPException(403, "Only fridge sensors post temperature readings")
    fridge = db.scalar(select(Fridge).where(Fridge.sensor_device_id == device.id))
    if fridge is None:
        raise HTTPException(409, "Sensor is not assigned to a fridge")
    now = utcnow()
    readings = sorted(body.readings, key=lambda r: r.ts or now)
    for r in readings:
        ts = to_utc_naive(r.ts) if r.ts else now
        if ts > now.replace(microsecond=0) and (ts - now).total_seconds() > 300:
            continue  # device clock is wrong; ignore future readings
        ingest_reading(db, fridge, ts, r.temp_c, r.door_open)
    device.last_seen_at, device.firmware = now, body.firmware or device.firmware
    db.commit()
    return {"accepted": len(readings), "fridge": fridge.name}


class TrayEventIn(BaseModel):
    ts: datetime | None = None
    weight_before_g: float
    weight_after_g: float


class TrayEventsIn(BaseModel):
    events: list[TrayEventIn] = Field(min_length=1, max_length=500)
    firmware: str | None = None


@router.post("/tray-events")
def post_tray_events(body: TrayEventsIn, db: Session = Depends(get_db), device: Device = Depends(current_device)):
    if device.kind != "weight_tray":
        raise HTTPException(403, "Only weight trays post tray events")
    tray = db.scalar(select(Tray).where(Tray.device_id == device.id))
    if tray is None:
        raise HTTPException(409, "Tray device is not assigned to a tray")
    now = utcnow()
    stored = 0
    for e in sorted(body.events, key=lambda e: e.ts or now):
        ts = to_utc_naive(e.ts) if e.ts else now
        if record_tray_event(db, tray, ts, e.weight_before_g, e.weight_after_g) is not None:
            stored += 1
    device.last_seen_at, device.firmware = now, body.firmware or device.firmware
    db.commit()
    return {"stored": stored, "tray": tray.label}


class TagUploadIn(BaseModel):
    box_code: str
    readings: list[Reading] = Field(min_length=1, max_length=5000)


@router.post("/delivery-tag")
def post_tag_readings(body: TagUploadIn, db: Session = Depends(get_db), device: Device = Depends(current_device)):
    """Gateway upload of a delivery box logger (e.g. the pharmacy's reader or a home hub)."""
    if device.kind != "gateway":
        raise HTTPException(403, "Only gateways upload delivery tag data")
    box = db.scalar(select(DeliveryBox).where(DeliveryBox.code == body.box_code))
    if box is None:
        raise HTTPException(404, "Unknown box")
    delivery = db.scalar(select(Delivery).where(Delivery.box_id == box.id, Delivery.status == "in_transit"))
    if delivery is None:
        raise HTTPException(409, "Box is not on a delivery")
    added = add_journey_readings(db, delivery, [(to_utc_naive(r.ts) if r.ts else utcnow(), r.temp_c)
                                                for r in body.readings])
    device.last_seen_at = utcnow()
    db.commit()
    return {"added": added}
