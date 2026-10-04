"""Plain-dict views of models for JSON responses."""

from __future__ import annotations

from ..models import (
    Alert,
    Delivery,
    Excursion,
    ExcursionDecision,
    Fridge,
    Home,
    Interview,
    MedicineItem,
    ReconciliationRecord,
    Resident,
    StabilityRule,
    Tray,
    User,
)


def user(u: User) -> dict:
    return {"id": u.id, "username": u.username, "display_name": u.display_name, "role": u.role,
            "home_id": u.home_id, "organisation_id": u.organisation_id, "on_shift": u.on_shift,
            "phone": u.phone, "registration_number": u.registration_number}


def home(h: Home) -> dict:
    return {"id": h.id, "name": h.name, "organisation_id": h.organisation_id, "pharmacy_id": h.pharmacy_id,
            "cqc_location_id": h.cqc_location_id, "timezone": h.timezone, "allow_resident_names": h.allow_resident_names,
            "uses_digital_mar": h.uses_digital_mar, "emar_system": h.emar_system,
            "reconcile_window_min": h.reconcile_window_min, "excursion_grace_min": h.excursion_grace_min,
            "sensor_offline_min": h.sensor_offline_min, "door_open_alert_min": h.door_open_alert_min}


def fridge(f: Fridge, open_excursion: Excursion | None = None) -> dict:
    status = "no_data"
    if f.last_temp_c is not None:
        status = "out_of_range" if (f.last_temp_c < f.min_c or f.last_temp_c > f.max_c) else "ok"
    return {"id": f.id, "home_id": f.home_id, "name": f.name, "min_c": f.min_c, "max_c": f.max_c,
            "last_temp_c": f.last_temp_c, "last_reading_at": f.last_reading_at, "status": status,
            "door_open_since": f.door_open_since, "has_sensor": f.sensor_device_id is not None,
            "open_excursion_id": open_excursion.id if open_excursion else None}


def resident(r: Resident) -> dict:
    return {"id": r.id, "ref": r.ref, "display_name": r.display_name, "room": r.room, "active": r.active}


def tray(t: Tray) -> dict:
    return {"id": t.id, "label": t.label, "fridge_id": t.fridge_id, "resident_id": t.resident_id,
            "resident_ref": t.resident.ref if t.resident else None, "current_weight_g": t.current_weight_g,
            "last_event_at": t.last_event_at, "has_device": t.device_id is not None}


def item(i: MedicineItem) -> dict:
    return {"id": i.id, "label_code": i.label_code, "medicine_name": i.medicine_name, "strength": i.strength,
            "resident_id": i.resident_id, "resident_ref": i.resident.ref if i.resident else None,
            "resident_name": i.resident.display_name if i.resident else None,
            "dose_instructions": i.dose_instructions, "dose_times": i.dose_times, "expiry": i.expiry,
            "adjusted_expiry": i.adjusted_expiry, "effective_expiry": i.effective_expiry,
            "tray_id": i.tray_id, "tray": i.tray.label if i.tray else None, "fridge_id": i.fridge_id,
            "fridge": i.fridge.name if i.fridge else None, "gtin": i.gtin, "batch": i.batch,
            "unit_weight_g": i.unit_weight_g, "awaiting_weight": i.awaiting_weight, "status": i.status,
            "minutes_out_of_range": i.minutes_out_of_range, "booked_in_at": i.booked_in_at}


def alert(a: Alert) -> dict:
    return {"id": a.id, "home_id": a.home_id, "type": a.type, "severity": a.severity, "title": a.title,
            "message": a.message, "raised_at": a.raised_at, "seen_by": a.seen_by, "seen_at": a.seen_at,
            "action_taken": a.action_taken, "resolved_by": a.resolved_by, "resolved_at": a.resolved_at,
            "context": a.context}


def excursion(e: Excursion, decisions: list[ExcursionDecision] | None = None) -> dict:
    out = {"id": e.id, "fridge_id": e.fridge_id, "fridge": e.fridge.name, "started_at": e.started_at,
           "ended_at": e.ended_at, "min_c": e.min_c, "max_c": e.max_c, "minutes_out": e.minutes_out,
           "data_gap": e.data_gap, "alerted": e.alerted, "assessed": e.assessed}
    if decisions is not None:
        out["decisions"] = [decision(d) for d in decisions]
    return out


def decision(d: ExcursionDecision) -> dict:
    return {"id": d.id, "excursion_id": d.excursion_id, "item_id": d.item_id, "decision": d.decision,
            "instruction": d.instruction, "rule_id": d.rule_id, "new_expiry": d.new_expiry,
            "acknowledged_by": d.acknowledged_by, "acknowledged_at": d.acknowledged_at,
            "item": item(d.item)}


def rule(r: StabilityRule) -> dict:
    return {"id": r.id, "name": r.name, "match_gtin": r.match_gtin, "match_name": r.match_name,
            "min_allowed_c": r.min_allowed_c, "max_allowed_c": r.max_allowed_c,
            "max_minutes_out": r.max_minutes_out, "new_expiry_days": r.new_expiry_days,
            "source_reference": r.source_reference, "notes": r.notes, "status": r.status,
            "approved_by_name": r.approved_by_name, "approved_at": r.approved_at, "usable": r.usable}


def recon(r: ReconciliationRecord) -> dict:
    return {"id": r.id, "tray_id": r.tray_id, "item_id": r.item_id, "outcome": r.outcome,
            "mismatch": r.mismatch, "detail": r.detail, "alert_id": r.alert_id, "created_at": r.created_at,
            "administration_id": r.administration_id}


def delivery(d: Delivery) -> dict:
    return {"id": d.id, "box": d.box.code, "home_id": d.home_id, "pharmacy_id": d.pharmacy_id,
            "dispatched_at": d.dispatched_at, "arrived_at": d.arrived_at, "status": d.status,
            "result_reason": d.result_reason, "contents_note": d.contents_note}


def interview(i: Interview) -> dict:
    return {"id": i.id, "group": i.group, "interviewee_label": i.interviewee_label,
            "organisation": i.organisation, "held_on": i.held_on,
            "score_stock_mismatch": i.score_stock_mismatch, "score_fridge_excursion": i.score_fridge_excursion,
            "score_delivery_temperature": i.score_delivery_temperature, "still_on_paper": i.still_on_paper,
            "money_or_time_spent": i.money_or_time_spent, "workarounds": i.workarounds, "notes": i.notes,
            "pilot_interest": i.pilot_interest, "written_up_at": i.written_up_at}
