"""Day-to-day operations: scanning, booking in, rounds, alerts, fridges, deliveries, audit."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import alerts as alert_svc
from .. import audit
from ..db import local, to_utc_naive, utcnow
from ..decision_engine import acknowledge, resolve_quarantine
from ..delivery import check_at_door, dispatch
from ..integrations.emar import import_administrations, parse_csv
from ..labels import LabelError, PharmacyLabel, build_pharmacy_label, parse_gs1, parse_pharmacy_label
from ..models import (
    Alert,
    Delivery,
    DeliveryBox,
    Excursion,
    ExcursionDecision,
    Fridge,
    MedicineItem,
    ReconciliationRecord,
    ReplacementRequest,
    Resident,
    TemperatureReading,
    TrayEvent,
    User,
)
from ..reconciliation import reconcile_home
from ..register import RegisterError, book_in, set_status
from ..rounds import AdministrationError, due_now, mar_chart, record_administration
from ..temperature import ingest_reading
from . import serialize as S
from .deps import MANAGERS, SENIORS, STAFF, current_user, get_db, home_or_404, owned, require

router = APIRouter(prefix="/api")


# ---- Scanning ------------------------------------------------------------

class ScanIn(BaseModel):
    text: str


@router.post("/labels/parse")
def parse_scan(body: ScanIn, user: User = Depends(current_user)):
    """Identify what was scanned: a pharmacy label or a manufacturer's pack code."""
    text = body.text.strip()
    try:
        if text.startswith("MV1|"):
            return {"kind": "pharmacy_label", "label": parse_pharmacy_label(text).__dict__}
        return {"kind": "gs1_pack", "pack": parse_gs1(text).__dict__}
    except LabelError as exc:
        raise HTTPException(422, str(exc)) from exc


class LabelBuildIn(BaseModel):
    label_code: str
    medicine_name: str
    strength: str | None = None
    resident_ref: str
    dose_instructions: str | None = None
    dose_times: list[str] = []
    expiry: date | None = None
    gtin: str | None = None


@router.post("/labels/build")
def build_label(body: LabelBuildIn, user: User = Depends(current_user)):
    """Produce the QR payload a pharmacy (or the home) prints on the label."""
    try:
        text = build_pharmacy_label(PharmacyLabel(**body.model_dump()))
        parse_pharmacy_label(text)  # validate round-trip
    except LabelError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"qr_text": text}


# ---- Register ------------------------------------------------------------

class BookInIn(BaseModel):
    label_text: str
    pack_text: str | None = None
    tray_id: int | None = None
    fridge_id: int | None = None
    delivery_id: int | None = None


@router.post("/homes/{home_id}/items/book-in")
def book_in_item(home_id: int, body: BookInIn, db: Session = Depends(get_db),
                 user: User = Depends(require(*STAFF))):
    home = home_or_404(db, user, home_id)
    try:
        label = parse_pharmacy_label(body.label_text)
        pack = parse_gs1(body.pack_text) if body.pack_text else None
        item = book_in(db, home, label, user, pack=pack, tray_id=body.tray_id, fridge_id=body.fridge_id,
                       delivery_id=body.delivery_id)
    except (LabelError, RegisterError) as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return S.item(item)


@router.get("/homes/{home_id}/items")
def list_items(home_id: int, status: str | None = None, resident_id: int | None = None,
               fridge_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    home_or_404(db, user, home_id)
    q = select(MedicineItem).where(MedicineItem.home_id == home_id)
    if status:
        q = q.where(MedicineItem.status == status)
    if resident_id:
        q = q.where(MedicineItem.resident_id == resident_id)
    if fridge_id:
        q = q.where(MedicineItem.fridge_id == fridge_id)
    return [S.item(i) for i in db.scalars(q.order_by(MedicineItem.medicine_name))]


@router.get("/items/{item_id}")
def get_item(item_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return S.item(owned(db, user, MedicineItem, item_id))


class ItemStatusIn(BaseModel):
    status: str
    reason: str = Field(min_length=3)


@router.post("/items/{item_id}/status")
def change_item_status(item_id: int, body: ItemStatusIn, db: Session = Depends(get_db),
                       user: User = Depends(require(*SENIORS))):
    item = owned(db, user, MedicineItem, item_id)
    if body.status not in ("finished", "returned_to_pharmacy", "discarded", "quarantined"):
        raise HTTPException(422, "Status must be finished, returned_to_pharmacy, discarded or quarantined")
    set_status(db, item, body.status, user, body.reason)
    db.commit()
    return S.item(item)


class QuarantineIn(BaseModel):
    outcome: str  # release | discard
    advised_by: str
    note: str | None = None
    new_expiry: date | None = None


@router.post("/items/{item_id}/quarantine-resolution")
def quarantine_resolution(item_id: int, body: QuarantineIn, db: Session = Depends(get_db),
                          user: User = Depends(require("pharmacist", *SENIORS))):
    item = owned(db, user, MedicineItem, item_id)
    try:
        resolve_quarantine(db, item, user, body.outcome, body.advised_by, body.note, body.new_expiry)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return S.item(item)


# ---- Rounds --------------------------------------------------------------

@router.get("/homes/{home_id}/round")
def round_view(home_id: int, window_min: int = Query(60, ge=15, le=240), db: Session = Depends(get_db),
               user: User = Depends(current_user)):
    home = home_or_404(db, user, home_id)
    return due_now(db, home, window_min=window_min)


class AdministrationIn(BaseModel):
    outcome: str
    notes: str | None = None
    ts: datetime | None = None


@router.post("/items/{item_id}/administrations")
def record_admin(item_id: int, body: AdministrationIn, db: Session = Depends(get_db),
                 user: User = Depends(require(*STAFF))):
    item = owned(db, user, MedicineItem, item_id)
    ts = to_utc_naive(body.ts) if body.ts else utcnow()
    if ts > utcnow() + timedelta(minutes=5):
        raise HTTPException(422, "Cannot record a dose in the future")
    try:
        adm = record_administration(db, item, body.outcome, user=user, ts=ts, notes=body.notes)
    except AdministrationError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return {"id": adm.id, "item_id": item.id, "outcome": adm.outcome, "ts": adm.ts}


@router.get("/homes/{home_id}/mar")
def get_mar(home_id: int, resident_id: int, start: date | None = None, days: int = Query(7, ge=1, le=31),
            db: Session = Depends(get_db), user: User = Depends(current_user)):
    home = home_or_404(db, user, home_id)
    resident = db.get(Resident, resident_id)
    if resident is None or resident.home_id != home_id:
        raise HTTPException(404, "Resident not found")
    start = start or (local(utcnow(), home.timezone).date() - timedelta(days=days - 1))
    return mar_chart(db, home, resident, start, days)


@router.post("/homes/{home_id}/emar/import")
def emar_import(home_id: int, csv_text: str = Body(..., media_type="text/csv"), db: Session = Depends(get_db),
                user: User = Depends(require(*SENIORS))):
    """Upload the administration export from the home's eMAR (CSV)."""
    home = home_or_404(db, user, home_id)
    rows, errors = parse_csv(csv_text)
    result = import_administrations(db, home, rows, "emar_import")
    result["errors"] = errors + result["errors"]
    audit.record(db, "emar.imported", actor=user, home_id=home.id,
                 details={"imported": result["imported"], "duplicates": result["duplicates_skipped"],
                          "errors": len(result["errors"])})
    db.commit()
    return result


# ---- Reconciliation ------------------------------------------------------

@router.post("/homes/{home_id}/reconcile")
def run_reconcile(home_id: int, db: Session = Depends(get_db), user: User = Depends(require(*SENIORS))):
    home = home_or_404(db, user, home_id)
    records = reconcile_home(db, home)
    db.commit()
    return [S.recon(r) for r in records]


@router.get("/homes/{home_id}/reconciliation")
def list_reconciliation(home_id: int, mismatch_only: bool = False, days: int = Query(7, ge=1, le=365),
                        db: Session = Depends(get_db), user: User = Depends(current_user)):
    home_or_404(db, user, home_id)
    q = select(ReconciliationRecord).where(ReconciliationRecord.home_id == home_id,
                                           ReconciliationRecord.created_at >= utcnow() - timedelta(days=days))
    if mismatch_only:
        q = q.where(ReconciliationRecord.mismatch.is_(True))
    return [S.recon(r) for r in db.scalars(q.order_by(ReconciliationRecord.id.desc()))]


@router.get("/trays/{tray_id}/events")
def tray_events(tray_id: int, limit: int = Query(50, le=500), db: Session = Depends(get_db),
                user: User = Depends(current_user)):
    from ..models import Tray

    owned(db, user, Tray, tray_id)
    rows = db.scalars(select(TrayEvent).where(TrayEvent.tray_id == tray_id).order_by(TrayEvent.ts.desc()).limit(limit))
    return [{"id": e.id, "ts": e.ts, "kind": e.kind, "delta_g": e.delta_g, "weight_after_g": e.weight_after_g,
             "reconciled": e.reconciled, "note": e.note} for e in rows]


# ---- Alerts --------------------------------------------------------------

@router.get("/homes/{home_id}/alerts")
def list_alerts(home_id: int, open_only: bool = True, limit: int = Query(100, le=1000),
                db: Session = Depends(get_db), user: User = Depends(current_user)):
    home_or_404(db, user, home_id)
    q = select(Alert).where(Alert.home_id == home_id)
    if open_only:
        q = q.where(Alert.resolved_at.is_(None))
    return [S.alert(a) for a in db.scalars(q.order_by(Alert.raised_at.desc()).limit(limit))]


@router.post("/alerts/{alert_id}/seen")
def alert_seen(alert_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    a = owned(db, user, Alert, alert_id)
    alert_svc.mark_seen(db, a, user)
    db.commit()
    return S.alert(a)


class ResolveIn(BaseModel):
    action_taken: str = Field(min_length=3)


@router.post("/alerts/{alert_id}/resolve")
def alert_resolve(alert_id: int, body: ResolveIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    a = owned(db, user, Alert, alert_id)
    if a.resolved_at:
        raise HTTPException(409, "Already resolved")
    if a.type == "stock_mismatch" and user.role == "carer":
        raise HTTPException(403, "Stock mismatches are resolved by the senior on shift")
    alert_svc.resolve(db, a, user, body.action_taken)
    db.commit()
    return S.alert(a)


# ---- Fridges and excursions ------------------------------------------------

@router.get("/fridges/{fridge_id}/readings")
def fridge_readings(fridge_id: int, hours: int = Query(24, ge=1, le=24 * 31), db: Session = Depends(get_db),
                    user: User = Depends(current_user)):
    owned(db, user, Fridge, fridge_id)
    since = utcnow() - timedelta(hours=hours)
    rows = db.scalars(select(TemperatureReading).where(TemperatureReading.fridge_id == fridge_id,
                                                       TemperatureReading.ts >= since).order_by(TemperatureReading.ts))
    return [{"ts": r.ts, "temp_c": r.temp_c, "door_open": r.door_open, "source": r.source} for r in rows]


class ManualReadingIn(BaseModel):
    temp_c: float = Field(ge=-40, le=60)
    ts: datetime | None = None


@router.post("/fridges/{fridge_id}/manual-reading")
def manual_reading(fridge_id: int, body: ManualReadingIn, db: Session = Depends(get_db),
                   user: User = Depends(require(*STAFF))):
    """Hand-recorded reading (sensor down, or homes still doing daily checks)."""
    fridge = owned(db, user, Fridge, fridge_id)
    ts = to_utc_naive(body.ts) if body.ts else utcnow()
    r = ingest_reading(db, fridge, ts, body.temp_c, source="manual")
    audit.record(db, "temperature.manual_reading", actor=user, home_id=fridge.home_id, entity_type="fridge",
                 entity_id=fridge.id, details={"temp_c": body.temp_c, "ts": ts})
    db.commit()
    return {"id": r.id, "ts": r.ts, "temp_c": r.temp_c}


@router.get("/homes/{home_id}/excursions")
def list_excursions(home_id: int, days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db),
                    user: User = Depends(current_user)):
    home_or_404(db, user, home_id)
    rows = db.scalars(select(Excursion).join(Fridge).where(Fridge.home_id == home_id,
                                                           Excursion.started_at >= utcnow() - timedelta(days=days))
                      .order_by(Excursion.started_at.desc()))
    return [S.excursion(e) for e in rows]


@router.get("/excursions/{excursion_id}")
def get_excursion(excursion_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    e = db.get(Excursion, excursion_id)
    if e is None:
        raise HTTPException(404, "Not found")
    home_or_404(db, user, e.fridge.home_id)
    decisions = list(db.scalars(select(ExcursionDecision).where(ExcursionDecision.excursion_id == e.id)))
    return S.excursion(e, decisions)


@router.post("/decisions/{decision_id}/acknowledge")
def ack_decision(decision_id: int, db: Session = Depends(get_db), user: User = Depends(require(*STAFF))):
    d = db.get(ExcursionDecision, decision_id)
    if d is None:
        raise HTTPException(404, "Not found")
    home_or_404(db, user, d.item.home_id)
    acknowledge(db, d, user)
    db.commit()
    return S.decision(d)


# ---- Deliveries ------------------------------------------------------------

class JourneyReading(BaseModel):
    ts: datetime
    temp_c: float


class DoorCheckIn(BaseModel):
    box_code: str
    readings: list[JourneyReading] | None = None


@router.post("/deliveries/check")
def door_check(body: DoorCheckIn, db: Session = Depends(get_db), user: User = Depends(require(*STAFF))):
    readings = [(to_utc_naive(r.ts), r.temp_c) for r in body.readings] if body.readings else None
    try:
        delivery, result = check_at_door(db, body.box_code.strip(), user, readings)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    db.commit()
    return {"result": "green" if result.ok else "red", "reason": result.reason, "min_c": result.min_c,
            "max_c": result.max_c, "readings": result.readings, "delivery": S.delivery(delivery)}


class DispatchIn(BaseModel):
    box_code: str
    home_id: int
    contents_note: str | None = None
    dispatched_at: datetime | None = None


@router.post("/deliveries/dispatch")
def dispatch_delivery(body: DispatchIn, db: Session = Depends(get_db),
                      user: User = Depends(require("pharmacist", "admin"))):
    box = db.scalar(select(DeliveryBox).where(DeliveryBox.code == body.box_code))
    if box is None:
        raise HTTPException(404, "Unknown box")
    home_or_404(db, user, body.home_id)
    try:
        d = dispatch(db, box, body.home_id, user, to_utc_naive(body.dispatched_at) if body.dispatched_at else None,
                     body.contents_note)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return S.delivery(d)


@router.get("/homes/{home_id}/deliveries")
def list_deliveries(home_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    home_or_404(db, user, home_id)
    rows = db.scalars(select(Delivery).where(Delivery.home_id == home_id).order_by(Delivery.dispatched_at.desc()).limit(100))
    return [S.delivery(d) for d in rows]


@router.get("/homes/{home_id}/replacements")
def list_replacements(home_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    home_or_404(db, user, home_id)
    rows = db.scalars(select(ReplacementRequest).where(ReplacementRequest.home_id == home_id)
                      .order_by(ReplacementRequest.created_at.desc()).limit(200))
    return [{"id": r.id, "item_id": r.item_id, "delivery_id": r.delivery_id, "reason": r.reason,
             "status": r.status, "created_at": r.created_at} for r in rows]


# ---- Audit -----------------------------------------------------------------

@router.get("/homes/{home_id}/audit")
def audit_log(home_id: int, limit: int = Query(200, le=2000), db: Session = Depends(get_db),
              user: User = Depends(require(*SENIORS))):
    from ..models import AuditEntry

    home_or_404(db, user, home_id)
    rows = db.scalars(select(AuditEntry).where(AuditEntry.home_id == home_id).order_by(AuditEntry.id.desc()).limit(limit))
    return [{"id": e.id, "ts": e.ts, "actor": e.actor, "action": e.action, "entity_type": e.entity_type,
             "entity_id": e.entity_id, "details": e.details} for e in rows]


@router.get("/homes/{home_id}/audit.csv", response_class=PlainTextResponse)
def audit_export(home_id: int, start: datetime | None = None, end: datetime | None = None,
                 db: Session = Depends(get_db), user: User = Depends(require(*MANAGERS))):
    home = home_or_404(db, user, home_id)
    audit.record(db, "audit.exported", actor=user, home_id=home.id, details={"start": start, "end": end})
    db.commit()
    csv_text = audit.export_csv(db, home.id, to_utc_naive(start) if start else None, to_utc_naive(end) if end else None)
    filename = f"medverify-audit-{home.id}-{utcnow():%Y%m%d}.csv"
    return PlainTextResponse(csv_text, media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/audit/verify")
def audit_verify(db: Session = Depends(get_db), user: User = Depends(require(*MANAGERS))):
    ok, bad = audit.verify_chain(db)
    return {"intact": ok, "first_bad_entry": bad}
