"""Pharmacy link: replacement orders.

Replacement requests are created by the decision engine (discarded items) and
by refused deliveries. This module sends the open ones to the pharmacy. Until
a PMR/ordering integration exists, that means a clear email/SMS per request,
kept in the notifications outbox for proof.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, notify
from ..models import Home, MedicineItem, Pharmacy, ReplacementRequest


def send_open_requests(db: Session) -> int:
    sent = 0
    for req in db.scalars(select(ReplacementRequest).where(ReplacementRequest.status == "requested")):
        pharmacy = db.get(Pharmacy, req.pharmacy_id) if req.pharmacy_id else None
        if pharmacy is None or not (pharmacy.email or pharmacy.phone):
            continue
        home = db.get(Home, req.home_id)
        item = db.get(MedicineItem, req.item_id) if req.item_id else None
        what = (f"{item.medicine_name} {item.strength or ''} (label {item.label_code}, resident "
                f"{item.resident.ref if item.resident else '?'})" if item else "items in the refused delivery")
        body = f"MedVerify replacement request #{req.id} from {home.name}: {what}. Reason: {req.reason}"
        if pharmacy.email:
            notify.queue(db, "email", pharmacy.email, body)
        if pharmacy.phone:
            notify.queue(db, "sms", pharmacy.phone, body)
        req.status = "sent"
        audit.record(db, "replacement.sent", home_id=req.home_id, entity_type="replacement_request",
                     entity_id=req.id, details={"pharmacy": pharmacy.name})
        sent += 1
    return sent
