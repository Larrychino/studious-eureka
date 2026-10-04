"""Reconciliation engine: what physically left the tray vs what staff recorded.

Weight trays report stable weight changes. A decrease is a removal, an increase
a return. Removal + return on the same tray within the window form one
"handling session". Each session is matched against administration records
(given / refused / spoilt) for items in that tray within the home's window.

Outcomes
--------
OK (logged only):
  matched_given        removed, and recorded as given
  matched_spoilt       removed, and recorded as spoilt
  matched_refused      removed, recorded as refused, and put back
  returned_unused      removed and put back at the same weight, nothing recorded
  booking_in           weight increase that calibrated a newly booked-in item
  refused_untouched    recorded as refused, never removed (nothing to reconcile)
  late_return          an item placed back after its session was closed

MISMATCH (alert to the senior on shift):
  removed_not_recorded removed and not put back, nothing recorded
  used_not_recorded    put back lighter (a dose was used), nothing recorded
  recorded_not_removed recorded as given/spoilt but nothing left the tray
  refused_not_returned recorded as refused, removed, never put back
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import alerts, audit
from .db import local, utcnow
from .models import Administration, Home, MedicineItem, ReconciliationRecord, Tray, TrayEvent

# Changes smaller than this are noise (condensation, tray settling).
NOISE_G = 1.5
MISMATCH_OUTCOMES = {"removed_not_recorded", "used_not_recorded", "recorded_not_removed", "refused_not_returned"}


@dataclass
class HandlingSession:
    tray_id: int
    removal: TrayEvent
    ret: TrayEvent | None = None
    administrations: list[Administration] = field(default_factory=list)

    @property
    def removed_g(self) -> float:
        return -self.removal.delta_g

    @property
    def net_change_g(self) -> float:
        """Weight lost overall (positive = something did not come back)."""
        return self.removed_g - (self.ret.delta_g if self.ret else 0.0)


def classify_tray_change(weight_before: float, weight_after: float) -> str | None:
    delta = weight_after - weight_before
    if abs(delta) < NOISE_G:
        return None
    return "removed" if delta < 0 else "returned"


def record_tray_event(db: Session, tray: Tray, ts: datetime, weight_before: float, weight_after: float) -> TrayEvent | None:
    """Store a stable weight change reported by a tray. Handles booking-in calibration."""
    kind = classify_tray_change(weight_before, weight_after)
    tray.current_weight_g, tray.last_event_at = weight_after, ts
    if kind is None:
        return None
    ev = TrayEvent(tray_id=tray.id, ts=ts, weight_before_g=weight_before, weight_after_g=weight_after,
                   delta_g=round(weight_after - weight_before, 2), kind=kind)
    db.add(ev)
    db.flush()
    if kind == "returned":
        pending = db.scalar(select(MedicineItem).where(MedicineItem.tray_id == tray.id,
                                                       MedicineItem.awaiting_weight.is_(True))
                            .order_by(MedicineItem.booked_in_at.desc()).limit(1))
        if pending is not None and ts - pending.booked_in_at <= timedelta(minutes=15):
            pending.unit_weight_g = round(ev.delta_g, 1)
            pending.awaiting_weight = False
            ev.reconciled, ev.note = True, "booking_in"
            db.add(ReconciliationRecord(home_id=tray.home_id, tray_id=tray.id, item_id=pending.id,
                                        return_event_id=ev.id, outcome="booking_in",
                                        detail=f"Calibrated at {pending.unit_weight_g} g"))
            audit.record(db, "item.weighed_in", home_id=tray.home_id, entity_type="medicine_item",
                         entity_id=pending.id, details={"unit_weight_g": pending.unit_weight_g, "tray_id": tray.id}, ts=ts)
    return ev


def _tolerance(item_weight: float | None) -> float:
    return max(2.0, 0.05 * item_weight) if item_weight else 2.0


def _guess_item(items: list[MedicineItem], grams: float) -> MedicineItem | None:
    weighted = [i for i in items if i.unit_weight_g]
    if not weighted:
        return items[0] if len(items) == 1 else None
    best = min(weighted, key=lambda i: abs(i.unit_weight_g - grams))
    return best if abs(best.unit_weight_g - grams) <= max(_tolerance(best.unit_weight_g), 0.25 * best.unit_weight_g) else None


def _label(item: MedicineItem | None) -> str:
    if item is None:
        return "an unidentified item"
    who = f" for resident {item.resident.ref}" if item.resident else ""
    return f"{item.medicine_name}{' ' + item.strength if item.strength else ''}{who}"


def _build_sessions(events: list[TrayEvent], window: timedelta, now: datetime):
    """Pair removals with the following return on the same tray.

    Returns (mature sessions, pending sessions, orphan returns). Sessions whose removal is younger
    than the window are left for the next run, as the record may still arrive.
    """
    sessions: list[HandlingSession] = []
    orphans: list[TrayEvent] = []
    open_by_tray: dict[int, HandlingSession] = {}
    for ev in sorted(events, key=lambda e: (e.ts, e.id)):
        if ev.kind == "removed":
            prev = open_by_tray.pop(ev.tray_id, None)
            if prev is not None:
                sessions.append(prev)
            open_by_tray[ev.tray_id] = HandlingSession(ev.tray_id, ev)
        else:
            s = open_by_tray.pop(ev.tray_id, None)
            if s is not None and ev.ts - s.removal.ts <= window:
                s.ret = ev
                sessions.append(s)
            else:
                if s is not None:
                    sessions.append(s)
                orphans.append(ev)
    sessions.extend(open_by_tray.values())
    mature = [s for s in sessions if s.removal.ts + window <= now]
    pending = [s for s in sessions if s.removal.ts + window > now]
    return mature, pending, orphans


def reconcile_home(db: Session, home: Home, now: datetime | None = None) -> list[ReconciliationRecord]:
    now = now or utcnow()
    window = timedelta(minutes=home.reconcile_window_min)

    def hm(dt: datetime) -> str:
        return f"{local(dt, home.timezone):%H:%M}"

    trays = {t.id: t for t in db.scalars(select(Tray).where(Tray.home_id == home.id))}
    if not trays:
        events = []
    else:
        events = list(db.scalars(select(TrayEvent).where(TrayEvent.tray_id.in_(trays.keys()),
                                                         TrayEvent.reconciled.is_(False))))
    sessions, pending, orphans = _build_sessions(events, window, now)

    admins = list(db.scalars(select(Administration).join(MedicineItem)
                             .where(MedicineItem.home_id == home.id, Administration.reconciled.is_(False))
                             .order_by(Administration.ts)))
    items_by_tray: dict[int, list[MedicineItem]] = {}
    for item in db.scalars(select(MedicineItem).where(MedicineItem.home_id == home.id,
                                                      MedicineItem.tray_id.is_not(None))):
        items_by_tray.setdefault(item.tray_id, []).append(item)

    # Match each administration to the nearest unmatched session on its item's tray.
    used_admins: set[int] = set()
    for adm in admins:
        item = adm.item
        if item.tray_id is None:
            continue
        candidates = [s for s in sessions if s.tray_id == item.tray_id and not s.administrations
                      and abs((s.removal.ts - adm.ts).total_seconds()) <= window.total_seconds()]
        if candidates:
            best = min(candidates, key=lambda s: abs((s.removal.ts - adm.ts).total_seconds()))
            best.administrations.append(adm)
            used_admins.add(adm.id)

    records: list[ReconciliationRecord] = []

    def add(outcome: str, *, tray_id=None, item=None, removal=None, ret=None, adm=None, detail=""):
        rec = ReconciliationRecord(home_id=home.id, tray_id=tray_id, item_id=item.id if item else None,
                                   removal_event_id=removal.id if removal else None,
                                   return_event_id=ret.id if ret else None,
                                   administration_id=adm.id if adm else None, outcome=outcome,
                                   mismatch=outcome in MISMATCH_OUTCOMES, detail=detail, created_at=now)
        db.add(rec)
        db.flush()
        records.append(rec)
        for ev in (removal, ret):
            if ev is not None:
                ev.reconciled = True
        if adm is not None:
            adm.reconciled = True
        if rec.mismatch:
            tray = trays.get(tray_id) if tray_id else None
            title = {
                "removed_not_recorded": "Medicine taken out but not recorded",
                "used_not_recorded": "Dose used but not recorded",
                "recorded_not_removed": "Recorded as given but never taken out",
                "refused_not_returned": "Refused dose not put back",
            }[outcome]
            alert = alerts.raise_alert(
                db, home.id, "stock_mismatch", title, detail, severity="high",
                context={"reconciliation_id": rec.id, "tray_id": tray_id, "item_id": rec.item_id,
                         "tray": tray.label if tray else None},
                dedupe_key=f"recon:{rec.id}")
            rec.alert_id = alert.id
        audit.record(db, f"reconciliation.{outcome}", home_id=home.id, entity_type="reconciliation", entity_id=rec.id,
                     details={"tray_id": tray_id, "item_id": rec.item_id, "administration_id": rec.administration_id,
                              "removal_event_id": rec.removal_event_id, "return_event_id": rec.return_event_id,
                              "detail": detail})

    for s in sessions:
        tray = trays[s.tray_id]
        when = hm(s.removal.ts)
        if s.administrations:
            adm = s.administrations[0]
            item = adm.item
            if adm.outcome == "refused" and s.ret is None:
                add("refused_not_returned", tray_id=tray.id, item=item, removal=s.removal, adm=adm,
                    detail=f"{_label(item)} was recorded as refused at {hm(adm.ts)} but was not put back in "
                           f"{tray.label}. Find the item or record it as spoilt.")
            else:
                add(f"matched_{adm.outcome}", tray_id=tray.id, item=item, removal=s.removal, ret=s.ret, adm=adm,
                    detail=f"{_label(item)}: removed {when}, recorded {adm.outcome} {hm(adm.ts)}")
            continue
        candidates = items_by_tray.get(tray.id, [])
        item = _guess_item(candidates, s.removed_g)
        tolerance = _tolerance(item.unit_weight_g if item else None)
        if s.ret is not None and abs(s.net_change_g) <= tolerance:
            add("returned_unused", tray_id=tray.id, item=item, removal=s.removal, ret=s.ret,
                detail=f"{_label(item)} taken out at {when} and put back unused at {hm(s.ret.ts)}")
        elif s.ret is not None:
            add("used_not_recorded", tray_id=tray.id, item=item, removal=s.removal, ret=s.ret,
                detail=f"{_label(item)} was taken out of {tray.label} at {when} and put back "
                       f"{s.net_change_g:.1f} g lighter, but no dose was recorded. Check and record it.")
        else:
            add("removed_not_recorded", tray_id=tray.id, item=item, removal=s.removal,
                detail=f"{_label(item)} was taken out of {tray.label} at {when} and not put back, "
                       "and nothing was recorded. Check and record it.")

    for ev in orphans:
        tray = trays[ev.tray_id]
        item = _guess_item(items_by_tray.get(tray.id, []), ev.delta_g)
        add("late_return", tray_id=tray.id, item=item, ret=ev,
            detail=f"{_label(item)} placed back in {tray.label} at {hm(ev.ts)}")

    # Records old enough that any removal would have been seen by now.
    for adm in admins:
        if adm.id in used_admins or adm.reconciled or adm.ts + window > now:
            continue
        item = adm.item
        if any(p.tray_id == item.tray_id and abs((p.removal.ts - adm.ts).total_seconds()) <= window.total_seconds()
               for p in pending):
            continue  # a matching removal is still inside its window; decide next run
        if item.tray_id is None:
            adm.reconciled = True  # not tray-tracked, nothing to verify physically
            continue
        if adm.outcome == "refused":
            add("refused_untouched", tray_id=item.tray_id, item=item, adm=adm,
                detail=f"{_label(item)} recorded as refused at {hm(adm.ts)}; item never left the tray")
        else:
            add("recorded_not_removed", tray_id=item.tray_id, item=item, adm=adm,
                detail=f"{_label(item)} was recorded as {adm.outcome} at {hm(adm.ts)} but the tray shows it "
                       "was never taken out. Check the dose was really given and the right item was used.")
    return records


def reconcile_all(db: Session, now: datetime | None = None) -> int:
    count = 0
    for home in db.scalars(select(Home)):
        count += len(reconcile_home(db, home, now))
    return count
