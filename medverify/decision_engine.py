"""Excursion decision engine.

When a fridge leaves its range, every item in that fridge is checked against
pharmacist-approved stability rules and staff get one plain instruction per
item: use, use with a new expiry, discard, or quarantine.

Safety rules baked in (from the plan's risk register):
  * Only rules with status 'approved', a named approver and a published source
    are applied. Draft or unsourced rules are ignored.
  * Whenever the system is unsure (no rule, gaps in the temperature record,
    unknown exposure) the answer is QUARANTINE and ask the pharmacist.
  * Exposure is cumulative per item across excursions.
  * Exposure is estimated conservatively: time between the last good reading
    and the first bad one counts as out of range.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import alerts, audit
from .models import (
    Excursion,
    ExcursionDecision,
    Fridge,
    MedicineItem,
    ReplacementRequest,
    StabilityRule,
    TemperatureReading,
    User,
)

MAX_READING_GAP_MIN = 30.0


@dataclass
class Exposure:
    started_at: datetime
    ended_at: datetime
    min_c: float
    max_c: float
    minutes_out: float
    data_gap: bool


@dataclass
class Decision:
    decision: str
    instruction: str
    new_expiry: date | None = None
    rule: StabilityRule | None = None


def compute_exposure(readings: list[TemperatureReading], min_c: float, max_c: float,
                     started_at: datetime, ended_at: datetime,
                     max_gap_min: float = MAX_READING_GAP_MIN) -> Exposure:
    """Time-weighted exposure for one excursion.

    `readings` should include the last in-range reading before the excursion
    (if any) and the first in-range reading after it.
    """
    pts = sorted(readings, key=lambda r: r.ts)
    if not pts:
        return Exposure(started_at, ended_at, float("nan"), float("nan"), 0.0, True)

    def out(r: TemperatureReading) -> bool:
        return r.temp_c < min_c or r.temp_c > max_c

    minutes = 0.0
    gap = False
    for a, b in zip(pts, pts[1:]):
        span = (b.ts - a.ts).total_seconds() / 60.0
        if out(a) or out(b):
            minutes += span
            if span > max_gap_min:
                gap = True
    out_pts = [r.temp_c for r in pts if out(r)] or [r.temp_c for r in pts]
    # No closing in-range reading yet: the excursion is still running.
    if out(pts[-1]):
        tail = (ended_at - pts[-1].ts).total_seconds() / 60.0
        if tail > 0:
            minutes += tail
            if tail > max_gap_min:
                gap = True
    return Exposure(started_at, ended_at, min(out_pts), max(out_pts), round(minutes, 1), gap)


def match_rule(db: Session, item: MedicineItem) -> StabilityRule | None:
    rules = [r for r in db.scalars(select(StabilityRule).where(StabilityRule.status == "approved")) if r.usable]
    if item.gtin:
        for r in rules:
            if r.match_gtin and r.match_gtin == item.gtin:
                return r
    name = (item.medicine_name or "").lower()
    best: StabilityRule | None = None
    for r in rules:
        if r.match_name and r.match_name.lower() in name:
            if best is None or len(r.match_name) > len(best.match_name or ""):
                best = r
    return best


def decide(item: MedicineItem, rule: StabilityRule | None, exposure: Exposure, today: date) -> Decision:
    label = f"{item.medicine_name}{' ' + item.strength if item.strength else ''} (label {item.label_code})"
    expiry = item.effective_expiry
    if expiry and expiry < today:
        return Decision("discard", f"DISCARD {label}: it expired on {expiry:%d %b %Y}. Request a replacement.", rule=rule)
    if rule is None:
        return Decision("quarantine",
                        f"QUARANTINE {label}: there is no pharmacist-approved stability rule for this medicine. "
                        "Keep it in a working fridge in a bag marked 'Do not use' and ask the pharmacist.")
    if exposure.data_gap:
        return Decision("quarantine",
                        f"QUARANTINE {label}: the temperature record has gaps, so safety cannot be confirmed. "
                        "Mark 'Do not use' and ask the pharmacist.", rule=rule)
    total = item.minutes_out_of_range + exposure.minutes_out
    if exposure.min_c < rule.min_allowed_c:
        return Decision("discard",
                        f"DISCARD {label}: it reached {exposure.min_c:.1f}°C, below the safe minimum of "
                        f"{rule.min_allowed_c:.1f}°C (it may have frozen). Request a replacement.", rule=rule)
    if exposure.max_c > rule.max_allowed_c:
        return Decision("discard",
                        f"DISCARD {label}: it reached {exposure.max_c:.1f}°C, above the safe maximum of "
                        f"{rule.max_allowed_c:.1f}°C. Request a replacement.", rule=rule)
    if total > rule.max_minutes_out:
        return Decision("discard",
                        f"DISCARD {label}: it has now spent {total:.0f} minutes out of the fridge range in total, "
                        f"more than the {rule.max_minutes_out:.0f} minutes allowed. Request a replacement.", rule=rule)
    if rule.new_expiry_days is not None:
        new_expiry = exposure.started_at.date() + timedelta(days=rule.new_expiry_days)
        if expiry:
            new_expiry = min(new_expiry, expiry)
        return Decision("use_new_expiry",
                        f"USE {label}, but write the new expiry {new_expiry:%d %b %Y} on the label "
                        f"({total:.0f} of {rule.max_minutes_out:.0f} minutes allowed used).",
                        new_expiry=new_expiry, rule=rule)
    return Decision("use", f"USE {label}: still within its approved limits "
                           f"({total:.0f} of {rule.max_minutes_out:.0f} minutes allowed used).", rule=rule)


def excursion_readings(db: Session, excursion: Excursion, until: datetime) -> list[TemperatureReading]:
    """Readings for the excursion plus the last good reading before it."""
    inside = list(db.scalars(select(TemperatureReading).where(
        TemperatureReading.fridge_id == excursion.fridge_id,
        TemperatureReading.ts >= excursion.started_at,
        TemperatureReading.ts <= until).order_by(TemperatureReading.ts)))
    before = db.scalar(select(TemperatureReading).where(
        TemperatureReading.fridge_id == excursion.fridge_id,
        TemperatureReading.ts < excursion.started_at).order_by(TemperatureReading.ts.desc()).limit(1))
    return ([before] if before else []) + inside


def assess_excursion(db: Session, excursion: Excursion, today: date | None = None) -> list[ExcursionDecision]:
    """Run the decision engine for a closed excursion. Idempotent."""
    if excursion.assessed:
        return list(db.scalars(select(ExcursionDecision).where(ExcursionDecision.excursion_id == excursion.id)))
    fridge: Fridge = excursion.fridge
    end = excursion.ended_at
    if end is None:
        raise ValueError("Excursion is still open")
    readings = excursion_readings(db, excursion, end)
    exposure = compute_exposure(readings, fridge.min_c, fridge.max_c, excursion.started_at, end)
    excursion.minutes_out = exposure.minutes_out
    excursion.min_c = min(excursion.min_c, exposure.min_c)
    excursion.max_c = max(excursion.max_c, exposure.max_c)
    excursion.data_gap = exposure.data_gap
    today = today or end.date()

    items = list(db.scalars(select(MedicineItem).where(MedicineItem.fridge_id == fridge.id,
                                                      MedicineItem.status.in_(("in_stock", "quarantined")))))
    decisions: list[ExcursionDecision] = []
    for item in items:
        d = decide(item, match_rule(db, item), exposure, today)
        item.minutes_out_of_range = round(item.minutes_out_of_range + exposure.minutes_out, 1)
        row = ExcursionDecision(excursion_id=excursion.id, item_id=item.id, decision=d.decision,
                                instruction=d.instruction, rule_id=d.rule.id if d.rule else None,
                                new_expiry=d.new_expiry)
        db.add(row)
        decisions.append(row)
        if d.decision == "use_new_expiry":
            item.adjusted_expiry = d.new_expiry
        elif d.decision in ("discard", "quarantine") and item.status == "in_stock":
            # Never give an item while it is being discarded or reviewed.
            item.status = "quarantined"
        if d.decision == "discard":
            home = fridge.home
            db.add(ReplacementRequest(home_id=fridge.home_id, pharmacy_id=home.pharmacy_id, item_id=item.id,
                                      reason=f"Temperature excursion in {fridge.name}: {d.instruction}"))
    excursion.assessed = True
    db.flush()
    audit.record(db, "excursion.assessed", home_id=fridge.home_id, entity_type="excursion", entity_id=excursion.id,
                 details={"fridge": fridge.name, "start": excursion.started_at, "end": end,
                          "min_c": exposure.min_c, "max_c": exposure.max_c, "minutes_out": exposure.minutes_out,
                          "data_gap": exposure.data_gap,
                          "decisions": [{"item_id": r.item_id, "decision": r.decision, "rule_id": r.rule_id}
                                        for r in decisions]})

    needs_action = [r for r in decisions if r.decision != "use"]
    if needs_action or excursion.alerted:
        counts = {k: sum(1 for r in decisions if r.decision == k) for k in ("use", "use_new_expiry", "discard", "quarantine")}
        summary = (f"{counts['use']} use, {counts['use_new_expiry']} use with new expiry, "
                   f"{counts['discard']} discard, {counts['quarantine']} quarantine")
        alerts.raise_alert(
            db, fridge.home_id, "excursion_decision",
            f"{fridge.name}: what to do with each medicine",
            f"Fridge was out of range for {exposure.minutes_out:.0f} min "
            f"({exposure.min_c:.1f}°C to {exposure.max_c:.1f}°C). {summary}. Open the app for item-by-item instructions.",
            severity="critical" if counts["discard"] or counts["quarantine"] else "medium",
            context={"excursion_id": excursion.id, "fridge_id": fridge.id},
            dedupe_key=f"excursion-decision:{excursion.id}",
        )
    return decisions


def acknowledge(db: Session, decision: ExcursionDecision, user: User) -> ExcursionDecision:
    """Staff confirm they carried out the instruction."""
    from .db import utcnow
    from .register import set_status

    if decision.acknowledged_at:
        return decision
    decision.acknowledged_by, decision.acknowledged_at = user.id, utcnow()
    item = decision.item
    if decision.decision == "discard":
        set_status(db, item, "discarded", user, "Discarded after temperature excursion")
    audit.record(db, "excursion.decision_acknowledged", actor=user, home_id=item.home_id,
                 entity_type="excursion_decision", entity_id=decision.id,
                 details={"item_id": item.id, "decision": decision.decision})
    return decision


def resolve_quarantine(db: Session, item: MedicineItem, user: User, outcome: str, advised_by: str,
                       note: str | None = None, new_expiry: date | None = None) -> MedicineItem:
    """Record the pharmacist's answer for a quarantined item."""
    from .register import set_status

    if item.status != "quarantined":
        raise ValueError("Item is not quarantined")
    if not advised_by.strip():
        raise ValueError("Record the name of the pharmacist who advised")
    if outcome == "release":
        if new_expiry:
            item.adjusted_expiry = new_expiry
        set_status(db, item, "in_stock", user, f"Released on advice of {advised_by}")
    elif outcome == "discard":
        set_status(db, item, "discarded", user, f"Discarded on advice of {advised_by}")
        db.add(ReplacementRequest(home_id=item.home_id, pharmacy_id=item.fridge.home.pharmacy_id if item.fridge else None,
                                  item_id=item.id, reason=f"Discarded after quarantine on advice of {advised_by}"))
    else:
        raise ValueError("Outcome must be 'release' or 'discard'")
    audit.record(db, "item.quarantine_resolved", actor=user, home_id=item.home_id, entity_type="medicine_item",
                 entity_id=item.id, details={"outcome": outcome, "advised_by": advised_by, "note": note,
                                             "new_expiry": new_expiry})
    return item
