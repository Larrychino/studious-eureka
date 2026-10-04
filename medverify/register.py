"""Medicine register: what is where and whose it is."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit
from .db import utcnow
from .labels import GS1Pack, PharmacyLabel
from .models import Home, MedicineItem, Resident, Tray, User


class RegisterError(ValueError):
    pass


def get_or_create_resident(db: Session, home: Home, ref: str, display_name: str | None = None) -> Resident:
    if display_name and not home.allow_resident_names:
        raise RegisterError("This home's data agreement does not allow resident names to be stored; use the internal ID only")
    resident = db.scalar(select(Resident).where(Resident.home_id == home.id, Resident.ref == ref))
    if resident is None:
        resident = Resident(home_id=home.id, ref=ref, display_name=display_name)
        db.add(resident)
        db.flush()
    return resident


def tray_for_resident(db: Session, home_id: int, resident_id: int) -> Tray | None:
    return db.scalar(select(Tray).where(Tray.home_id == home_id, Tray.resident_id == resident_id))


def book_in(db: Session, home: Home, label: PharmacyLabel, user: User, *, pack: GS1Pack | None = None,
            tray_id: int | None = None, fridge_id: int | None = None, delivery_id: int | None = None,
            unit_weight_g: float | None = None) -> MedicineItem:
    """Register a scanned item. Places it in the resident's tray unless a tray is given."""
    if db.scalar(select(MedicineItem).where(MedicineItem.home_id == home.id,
                                            MedicineItem.label_code == label.label_code)):
        raise RegisterError(f"Label {label.label_code} is already booked in")
    resident = get_or_create_resident(db, home, label.resident_ref)
    tray = db.get(Tray, tray_id) if tray_id else tray_for_resident(db, home.id, resident.id)
    if tray is not None and tray.home_id != home.id:
        raise RegisterError("Tray belongs to another home")
    if tray is not None and tray.resident_id not in (None, resident.id):
        raise RegisterError("That tray belongs to a different resident")
    if tray is not None:
        fridge_id = tray.fridge_id
    if fridge_id is None:
        raise RegisterError("Choose a fridge or a tray for this item")

    expiry = label.expiry
    if pack and pack.expiry:
        expiry = min(d for d in (expiry, pack.expiry) if d is not None)
    if pack and pack.gtin and label.gtin and pack.gtin != label.gtin:
        raise RegisterError("The pack barcode does not match the pharmacy label. Check you have the right item")

    item = MedicineItem(
        home_id=home.id,
        label_code=label.label_code,
        medicine_name=label.medicine_name,
        strength=label.strength,
        resident_id=resident.id,
        dose_instructions=label.dose_instructions,
        dose_times=",".join(label.dose_times) or None,
        expiry=expiry,
        tray_id=tray.id if tray else None,
        fridge_id=fridge_id,
        gtin=(pack.gtin if pack and pack.gtin else label.gtin),
        batch=pack.batch if pack else None,
        serial=pack.serial if pack else None,
        unit_weight_g=unit_weight_g,
        # When placed on a tray, the next weight increase on that tray calibrates the item.
        awaiting_weight=bool(tray and unit_weight_g is None),
        booked_in_at=utcnow(),
        booked_in_by=user.id,
        delivery_id=delivery_id,
    )
    db.add(item)
    db.flush()
    audit.record(db, "item.booked_in", actor=user, home_id=home.id, entity_type="medicine_item", entity_id=item.id,
                 details={"label_code": item.label_code, "medicine": item.medicine_name, "strength": item.strength,
                          "resident_ref": resident.ref, "tray_id": item.tray_id, "fridge_id": item.fridge_id,
                          "expiry": item.expiry, "gtin": item.gtin, "batch": item.batch})
    return item


def set_status(db: Session, item: MedicineItem, status: str, user: User | str | None, reason: str) -> None:
    old = item.status
    item.status = status
    audit.record(db, "item.status_changed", actor=user, home_id=item.home_id, entity_type="medicine_item",
                 entity_id=item.id, details={"from": old, "to": status, "reason": reason})


def expiring_items(db: Session, home_id: int, within_days: int = 7) -> list[MedicineItem]:
    limit = date.today() + timedelta(days=within_days)
    items = db.scalars(select(MedicineItem).where(MedicineItem.home_id == home_id,
                                                  MedicineItem.status == "in_stock"))
    return [i for i in items if i.effective_expiry and i.effective_expiry <= limit]
