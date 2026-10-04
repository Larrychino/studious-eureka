"""Stability rules (pharmacist), dashboards, pricing and customer discovery."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit, discovery, pricing
from ..db import utcnow
from ..models import (
    Alert,
    Excursion,
    ExcursionDecision,
    Fridge,
    Home,
    Interview,
    MedicineItem,
    ReconciliationRecord,
    StabilityRule,
    Tray,
    User,
)
from ..register import expiring_items
from . import serialize as S
from .deps import MANAGERS, can_access_home, current_user, get_db, home_or_404, require

router = APIRouter(prefix="/api")


# ---- Stability rules ---------------------------------------------------------

class RuleIn(BaseModel):
    name: str
    match_gtin: str | None = None
    match_name: str | None = None
    min_allowed_c: float = 2.0
    max_allowed_c: float = 8.0
    max_minutes_out: float = Field(default=0.0, ge=0)
    new_expiry_days: int | None = Field(default=None, ge=0)
    source_reference: str | None = None
    notes: str | None = None


@router.get("/rules")
def list_rules(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return [S.rule(r) for r in db.scalars(select(StabilityRule).order_by(StabilityRule.name))]


@router.post("/rules")
def create_rule(body: RuleIn, db: Session = Depends(get_db), user: User = Depends(require("pharmacist", "admin"))):
    if not body.match_gtin and not body.match_name:
        raise HTTPException(422, "A rule must match a GTIN or a medicine name")
    if body.min_allowed_c >= body.max_allowed_c:
        raise HTTPException(422, "Minimum must be below maximum")
    r = StabilityRule(**body.model_dump(), status="draft")
    db.add(r)
    db.flush()
    audit.record(db, "rule.created", actor=user, entity_type="stability_rule", entity_id=r.id, details=body.model_dump())
    db.commit()
    return S.rule(r)


@router.patch("/rules/{rule_id}")
def edit_rule(rule_id: int, body: RuleIn, db: Session = Depends(get_db),
              user: User = Depends(require("pharmacist", "admin"))):
    r = db.get(StabilityRule, rule_id)
    if r is None:
        raise HTTPException(404, "Not found")
    for k, v in body.model_dump().items():
        setattr(r, k, v)
    # Any change needs fresh approval.
    r.status, r.approved_by_id, r.approved_by_name, r.approved_at = "draft", None, None, None
    audit.record(db, "rule.edited", actor=user, entity_type="stability_rule", entity_id=r.id, details=body.model_dump())
    db.commit()
    return S.rule(r)


@router.post("/rules/{rule_id}/approve")
def approve_rule(rule_id: int, db: Session = Depends(get_db), user: User = Depends(require("pharmacist"))):
    """Only a named pharmacist can approve, and only with a published source."""
    r = db.get(StabilityRule, rule_id)
    if r is None:
        raise HTTPException(404, "Not found")
    if not (r.source_reference or "").strip():
        raise HTTPException(422, "Add the published stability source (e.g. SmPC section 6.4) before approving")
    name = user.display_name + (f" (GPhC {user.registration_number})" if user.registration_number else "")
    r.status, r.approved_by_id, r.approved_by_name, r.approved_at = "approved", user.id, name, utcnow()
    audit.record(db, "rule.approved", actor=user, entity_type="stability_rule", entity_id=r.id,
                 details={"source": r.source_reference})
    db.commit()
    return S.rule(r)


@router.post("/rules/{rule_id}/retire")
def retire_rule(rule_id: int, db: Session = Depends(get_db), user: User = Depends(require("pharmacist", "admin"))):
    r = db.get(StabilityRule, rule_id)
    if r is None:
        raise HTTPException(404, "Not found")
    r.status = "retired"
    audit.record(db, "rule.retired", actor=user, entity_type="stability_rule", entity_id=r.id)
    db.commit()
    return S.rule(r)


# ---- Dashboard ------------------------------------------------------------

# Assumptions for "staff minutes saved", to be replaced with pilot measurements.
MINUTES_PER_MANUAL_FRIDGE_CHECK = 3      # per fridge per day
MINUTES_PER_EXCURSION_PHONE_DECISION = 30  # calling pharmacy/111 and waiting for an answer
MINUTES_PER_MISMATCH_INVESTIGATION = 20    # finding a stock discrepancy at a manual count


def home_summary(db: Session, home: Home, days: int = 7) -> dict:
    since = utcnow() - timedelta(days=days)
    fridges = list(db.scalars(select(Fridge).where(Fridge.home_id == home.id)))
    out_of_range = [f for f in fridges if f.last_temp_c is not None and not (f.min_c <= f.last_temp_c <= f.max_c)]
    offline = [f for f in fridges if f.sensor_device_id and
               (f.last_reading_at is None or utcnow() - f.last_reading_at > timedelta(minutes=home.sensor_offline_min))]
    open_alerts = list(db.scalars(select(Alert).where(Alert.home_id == home.id, Alert.resolved_at.is_(None))))
    recon = list(db.scalars(select(ReconciliationRecord).where(ReconciliationRecord.home_id == home.id,
                                                               ReconciliationRecord.created_at >= since)))
    mismatches = [r for r in recon if r.mismatch]
    excursions = list(db.scalars(select(Excursion).join(Fridge).where(Fridge.home_id == home.id,
                                                                      Excursion.started_at >= since)))
    decisions = list(db.scalars(select(ExcursionDecision).where(
        ExcursionDecision.excursion_id.in_([e.id for e in excursions] or [-1]))))
    fridge_alerts_handled = db.scalar(select(func.count()).select_from(Alert).where(
        Alert.home_id == home.id, Alert.type.in_(("fridge_out_of_range", "excursion_decision")),
        Alert.resolved_at.is_not(None), Alert.raised_at >= since)) or 0
    trays = db.scalar(select(func.count()).select_from(Tray).where(Tray.home_id == home.id)) or 0
    quarantined = db.scalar(select(func.count()).select_from(MedicineItem).where(
        MedicineItem.home_id == home.id, MedicineItem.status == "quarantined")) or 0
    sensored = sum(1 for f in fridges if f.sensor_device_id)
    minutes_saved = (sensored * days * MINUTES_PER_MANUAL_FRIDGE_CHECK
                     + sum(1 for e in excursions if e.assessed) * MINUTES_PER_EXCURSION_PHONE_DECISION
                     + len(mismatches) * MINUTES_PER_MISMATCH_INVESTIGATION)
    quote = pricing.home_quote(len(fridges), trays, paper_to_digital=home.uses_digital_mar and not home.emar_system)
    return {
        "home": S.home(home),
        "period_days": days,
        "fridges": len(fridges),
        "fridges_out_of_range": [f.name for f in out_of_range],
        "sensors_offline": [f.name for f in offline],
        "trays": trays,
        "open_alerts": len(open_alerts),
        "open_critical": sum(1 for a in open_alerts if a.severity == "critical"),
        "mismatches_caught": len(mismatches),
        "mismatches_by_type": {k: sum(1 for r in mismatches if r.outcome == k) for k in
                               {r.outcome for r in mismatches}},
        "handling_sessions_verified": sum(1 for r in recon if not r.mismatch and r.outcome != "booking_in"),
        "excursions": len(excursions),
        "excursion_decisions": {k: sum(1 for d in decisions if d.decision == k)
                                for k in ("use", "use_new_expiry", "discard", "quarantine")},
        "fridge_alerts_handled": fridge_alerts_handled,
        "items_quarantined": quarantined,
        "items_expiring_7d": len(expiring_items(db, home.id, 7)),
        "staff_minutes_saved_estimate": minutes_saved,
        "monthly_price_gbp": quote.monthly_gbp,
    }


@router.get("/dashboard")
def dashboard(days: int = Query(7, ge=1, le=90), db: Session = Depends(get_db),
              user: User = Depends(require("senior", *MANAGERS))):
    homes = [h for h in db.scalars(select(Home).order_by(Home.name)) if can_access_home(user, h)]
    summaries = [home_summary(db, h, days) for h in homes]
    totals = {k: sum(s[k] for s in summaries) for k in
              ("fridges", "trays", "open_alerts", "open_critical", "mismatches_caught", "excursions",
               "fridge_alerts_handled", "items_quarantined", "staff_minutes_saved_estimate", "monthly_price_gbp")}
    return {"homes": summaries, "totals": totals,
            "assumptions": {"minutes_per_manual_fridge_check": MINUTES_PER_MANUAL_FRIDGE_CHECK,
                            "minutes_per_excursion_phone_decision": MINUTES_PER_EXCURSION_PHONE_DECISION,
                            "minutes_per_mismatch_investigation": MINUTES_PER_MISMATCH_INVESTIGATION}}


# ---- Pricing -----------------------------------------------------------------

@router.get("/pricing/plans")
def plans():
    return pricing.PLANS


class QuoteIn(BaseModel):
    fridges: int = Field(ge=0)
    trays: int = Field(ge=0)
    paper_to_digital: bool = False
    hardware_cost_gbp: float | None = Field(default=None, ge=0)


@router.post("/pricing/quote")
def quote(body: QuoteIn):
    q = pricing.home_quote(body.fridges, body.trays, body.paper_to_digital, body.hardware_cost_gbp)
    return {**q.__dict__, "min_contract_months": pricing.MIN_CONTRACT_MONTHS,
            "homes_for_100k": pricing.homes_needed_for(100_000, q.monthly_gbp) if q.monthly_gbp else None}


@router.get("/homes/{home_id}/quote")
def home_quote(home_id: int, db: Session = Depends(get_db), user: User = Depends(require(*MANAGERS))):
    home = home_or_404(db, user, home_id)
    fridges = db.scalar(select(func.count()).select_from(Fridge).where(Fridge.home_id == home.id)) or 0
    trays = db.scalar(select(func.count()).select_from(Tray).where(Tray.home_id == home.id)) or 0
    q = pricing.home_quote(fridges, trays, paper_to_digital=home.uses_digital_mar and not home.emar_system)
    return q.__dict__


# ---- Customer discovery (Phase 1) ---------------------------------------------

class InterviewIn(BaseModel):
    group: str
    interviewee_label: str = Field(min_length=1, max_length=200)
    organisation: str | None = None
    held_on: date
    score_stock_mismatch: int = Field(ge=0, le=3)
    score_fridge_excursion: int = Field(ge=0, le=3)
    score_delivery_temperature: int = Field(ge=0, le=3)
    still_on_paper: bool | None = None
    money_or_time_spent: str | None = None
    workarounds: str | None = None
    notes: str | None = None
    pilot_interest: bool = False
    written_up: bool = True


@router.get("/discovery/interviews")
def list_interviews(db: Session = Depends(get_db), user: User = Depends(require("admin"))):
    return [S.interview(i) for i in db.scalars(select(Interview).order_by(Interview.held_on.desc()))]


@router.post("/discovery/interviews")
def add_interview(body: InterviewIn, db: Session = Depends(get_db), user: User = Depends(require("admin"))):
    if body.group not in discovery.GROUP_TARGETS:
        raise HTTPException(422, f"Group must be one of {', '.join(discovery.GROUP_TARGETS)}")
    data = body.model_dump(exclude={"written_up"})
    i = Interview(**data, created_by=user.id, written_up_at=utcnow() if body.written_up else None)
    db.add(i)
    db.commit()
    return S.interview(i)


@router.put("/discovery/interviews/{interview_id}")
def update_interview(interview_id: int, body: InterviewIn, db: Session = Depends(get_db),
                     user: User = Depends(require("admin"))):
    i = db.get(Interview, interview_id)
    if i is None:
        raise HTTPException(404, "Not found")
    for k, v in body.model_dump(exclude={"written_up"}).items():
        setattr(i, k, v)
    if body.written_up and i.written_up_at is None:
        i.written_up_at = utcnow()
    db.commit()
    return S.interview(i)


@router.delete("/discovery/interviews/{interview_id}")
def delete_interview(interview_id: int, db: Session = Depends(get_db), user: User = Depends(require("admin"))):
    i = db.get(Interview, interview_id)
    if i is None:
        raise HTTPException(404, "Not found")
    db.delete(i)
    db.commit()
    return {"ok": True}


@router.get("/discovery/summary")
def discovery_summary(db: Session = Depends(get_db), user: User = Depends(require("admin"))):
    return discovery.summarise(list(db.scalars(select(Interview))))
