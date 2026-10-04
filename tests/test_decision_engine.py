from datetime import date, timedelta

from sqlalchemy import select

from medverify.decision_engine import Exposure, compute_exposure, decide, resolve_quarantine
from medverify.models import Alert, ExcursionDecision, ReplacementRequest, TemperatureReading
from medverify.temperature import ingest_reading

from .conftest import T0, add_item, approved_rule


def readings(*pairs):
    return [TemperatureReading(ts=T0 + timedelta(minutes=m), temp_c=t) for m, t in pairs]


def test_exposure_is_time_weighted_and_conservative():
    # good at 0, bad at 10 and 20, good at 30 -> whole 30 min counts as out of range
    exp = compute_exposure(readings((0, 5), (10, 10), (20, 12), (30, 5)), 2, 8, T0 + timedelta(minutes=10),
                           T0 + timedelta(minutes=30))
    assert exp.minutes_out == 30 and exp.max_c == 12 and not exp.data_gap


def test_exposure_flags_gaps():
    exp = compute_exposure(readings((0, 5), (10, 10), (70, 5)), 2, 8, T0, T0 + timedelta(minutes=70))
    assert exp.data_gap


def make_exposure(min_c=9.0, max_c=12.0, minutes=60, gap=False):
    return Exposure(T0, T0 + timedelta(minutes=minutes), min_c, max_c, minutes, gap)


class Item:  # light stand-in for MedicineItem
    def __init__(self, name="Insulin aspart", minutes_out=0.0, expiry=date(2030, 1, 1)):
        self.medicine_name, self.strength, self.label_code = name, None, "L"
        self.minutes_out_of_range, self.effective_expiry = minutes_out, expiry


def test_no_rule_means_quarantine():
    assert decide(Item(), None, make_exposure(), date(2026, 3, 2)).decision == "quarantine"


def test_gap_means_quarantine(db):
    rule = approved_rule(db)
    assert decide(Item(), rule, make_exposure(gap=True), date(2026, 3, 2)).decision == "quarantine"


def test_within_limits_gets_new_expiry(db):
    rule = approved_rule(db)
    d = decide(Item(), rule, make_exposure(), date(2026, 3, 2))
    assert d.decision == "use_new_expiry" and d.new_expiry == T0.date() + timedelta(days=28)


def test_new_expiry_never_extends_original(db):
    rule = approved_rule(db)
    d = decide(Item(expiry=date(2026, 3, 10)), rule, make_exposure(), date(2026, 3, 2))
    assert d.new_expiry == date(2026, 3, 10)


def test_too_hot_too_cold_too_long(db):
    rule = approved_rule(db)
    today = date(2026, 3, 2)
    assert decide(Item(), rule, make_exposure(max_c=31), today).decision == "discard"
    assert decide(Item(), rule, make_exposure(min_c=-1, max_c=1), today).decision == "discard"
    assert decide(Item(minutes_out=580), rule, make_exposure(minutes=30), today).decision == "discard"  # cumulative


def test_expired_item_discarded(db):
    rule = approved_rule(db)
    assert decide(Item(expiry=date(2026, 3, 1)), rule, make_exposure(), date(2026, 3, 2)).decision == "discard"


def test_draft_or_unsourced_rules_are_never_used(world):
    db = world["db"]
    approved_rule(db, status="draft")
    approved_rule(db, source_reference=None)
    item = add_item(world)
    fridge = world["fridge"]
    for m, t in [(0, 5), (10, 11), (20, 11), (30, 5)]:
        ingest_reading(db, fridge, T0 + timedelta(minutes=m), t)
    d = db.scalar(select(ExcursionDecision).where(ExcursionDecision.item_id == item.id))
    assert d.decision == "quarantine"
    assert item.status == "quarantined"


def test_full_excursion_flow(world):
    db, fridge = world["db"], world["fridge"]
    approved_rule(db)
    insulin = add_item(world, code="A")
    approved_rule(db, name="Strict", match_name="vaccine", max_allowed_c=8.0, max_minutes_out=0)
    vaccine = add_item(world, code="B", name="Flu vaccine")
    for m, t in [(0, 5), (10, 10), (20, 12), (30, 11), (40, 5)]:
        ingest_reading(db, fridge, T0 + timedelta(minutes=m), t)
    decisions = {d.item_id: d for d in db.scalars(select(ExcursionDecision))}
    assert decisions[insulin.id].decision == "use_new_expiry"
    assert decisions[vaccine.id].decision == "discard"
    assert insulin.minutes_out_of_range == 40
    assert vaccine.status == "quarantined"  # held until staff confirm the discard
    assert db.scalar(select(ReplacementRequest).where(ReplacementRequest.item_id == vaccine.id))
    types = {a.type for a in db.scalars(select(Alert))}
    assert {"fridge_out_of_range", "excursion_decision"} <= types


def test_short_blip_does_not_page_but_is_assessed(world):
    db, fridge = world["db"], world["fridge"]
    approved_rule(db)
    add_item(world)
    ingest_reading(db, fridge, T0, 5)
    ingest_reading(db, fridge, T0 + timedelta(minutes=2), 8.6)
    ingest_reading(db, fridge, T0 + timedelta(minutes=4), 6)
    assert db.scalar(select(Alert).where(Alert.type == "fridge_out_of_range")) is None
    d = db.scalar(select(ExcursionDecision))
    assert d.decision == "use_new_expiry"


def test_freezing_alerts_immediately(world):
    db, fridge = world["db"], world["fridge"]
    ingest_reading(db, fridge, T0, 5)
    ingest_reading(db, fridge, T0 + timedelta(minutes=1), -0.5)
    assert db.scalar(select(Alert).where(Alert.type == "fridge_out_of_range")) is not None


def test_quarantine_resolution_needs_named_pharmacist(world):
    import pytest

    db = world["db"]
    item = add_item(world)
    item.status = "quarantined"
    with pytest.raises(ValueError):
        resolve_quarantine(db, item, world["senior"], "release", "  ")
    resolve_quarantine(db, item, world["senior"], "release", "J Smith (GPhC 2012345)")
    assert item.status == "in_stock"
