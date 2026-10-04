from datetime import timedelta

from sqlalchemy import select

from medverify.models import Alert, Notification, ReconciliationRecord
from medverify.reconciliation import reconcile_home, record_tray_event
from medverify.rounds import record_administration

from .conftest import T0, add_item


def outcomes(db):
    return sorted(r.outcome for r in db.scalars(select(ReconciliationRecord)))


def test_removed_given_returned_is_matched(world):
    db, tray = world["db"], world["tray"]
    item = add_item(world)
    record_tray_event(db, tray, T0, 100.0, 76.0)
    record_administration(db, item, "given", user=world["carer"], ts=T0 + timedelta(minutes=1))
    record_tray_event(db, tray, T0 + timedelta(minutes=3), 76.0, 99.2)
    recs = reconcile_home(db, world["home"], T0 + timedelta(hours=1))
    assert [r.outcome for r in recs] == ["matched_given"]
    assert not recs[0].mismatch


def test_removed_not_recorded_alerts_senior_on_shift(world):
    db, tray = world["db"], world["tray"]
    add_item(world)
    record_tray_event(db, tray, T0, 100.0, 76.0)
    recs = reconcile_home(db, world["home"], T0 + timedelta(hours=1))
    assert [r.outcome for r in recs] == ["removed_not_recorded"]
    alert = db.scalar(select(Alert).where(Alert.type == "stock_mismatch"))
    assert alert is not None and "Insulin" in alert.message
    recipients = {n.recipient for n in db.scalars(select(Notification))}
    assert f"user:{world['senior'].id}" in recipients
    assert f"user:{world['carer'].id}" not in recipients  # mismatches go to the senior
    assert "+447700900000" in recipients  # and by text


def test_recorded_but_never_removed(world):
    db = world["db"]
    item = add_item(world)
    record_administration(db, item, "given", user=world["carer"], ts=T0)
    recs = reconcile_home(db, world["home"], T0 + timedelta(hours=1))
    assert [r.outcome for r in recs] == ["recorded_not_removed"]
    assert recs[0].mismatch


def test_returned_unused_is_logged_not_alerted(world):
    db, tray = world["db"], world["tray"]
    add_item(world)
    record_tray_event(db, tray, T0, 100.0, 76.0)
    record_tray_event(db, tray, T0 + timedelta(minutes=2), 76.0, 100.0)
    recs = reconcile_home(db, world["home"], T0 + timedelta(hours=1))
    assert [r.outcome for r in recs] == ["returned_unused"]
    assert db.scalar(select(Alert)) is None


def test_returned_lighter_without_record_is_mismatch(world):
    db, tray = world["db"], world["tray"]
    add_item(world, weight=120.0, name="Amoxicillin suspension")
    record_tray_event(db, tray, T0, 200.0, 80.0)
    record_tray_event(db, tray, T0 + timedelta(minutes=2), 80.0, 190.0)  # 10 g used
    recs = reconcile_home(db, world["home"], T0 + timedelta(hours=1))
    assert [r.outcome for r in recs] == ["used_not_recorded"]


def test_refused_must_be_put_back(world):
    db, tray = world["db"], world["tray"]
    item = add_item(world)
    record_tray_event(db, tray, T0, 100.0, 76.0)
    record_administration(db, item, "refused", user=world["carer"], ts=T0 + timedelta(minutes=1))
    recs = reconcile_home(db, world["home"], T0 + timedelta(hours=1))
    assert [r.outcome for r in recs] == ["refused_not_returned"]


def test_refused_and_returned_is_fine(world):
    db, tray = world["db"], world["tray"]
    item = add_item(world)
    record_tray_event(db, tray, T0, 100.0, 76.0)
    record_administration(db, item, "refused", user=world["carer"], ts=T0 + timedelta(minutes=1))
    record_tray_event(db, tray, T0 + timedelta(minutes=2), 76.0, 100.0)
    recs = reconcile_home(db, world["home"], T0 + timedelta(hours=1))
    assert [r.outcome for r in recs] == ["matched_refused"]


def test_waits_for_window_before_deciding(world):
    db, tray = world["db"], world["tray"]
    item = add_item(world)
    record_tray_event(db, tray, T0, 100.0, 76.0)
    assert reconcile_home(db, world["home"], T0 + timedelta(minutes=10)) == []  # record may still come
    record_administration(db, item, "given", user=world["carer"], ts=T0 + timedelta(minutes=12))
    recs = reconcile_home(db, world["home"], T0 + timedelta(hours=1))
    assert [r.outcome for r in recs] == ["matched_given"]


def test_record_before_pending_removal_is_not_flagged(world):
    """Recorded at 09:00, removed at 09:20, checked at 09:35: must not flag 'never removed'."""
    db, tray = world["db"], world["tray"]
    item = add_item(world)
    record_administration(db, item, "given", user=world["carer"], ts=T0)
    record_tray_event(db, tray, T0 + timedelta(minutes=20), 100.0, 76.0)
    assert reconcile_home(db, world["home"], T0 + timedelta(minutes=35)) == []
    recs = reconcile_home(db, world["home"], T0 + timedelta(hours=1))
    assert [r.outcome for r in recs] == ["matched_given"]


def test_reconciliation_is_idempotent(world):
    db, tray = world["db"], world["tray"]
    add_item(world)
    record_tray_event(db, tray, T0, 100.0, 76.0)
    reconcile_home(db, world["home"], T0 + timedelta(hours=1))
    assert reconcile_home(db, world["home"], T0 + timedelta(hours=2)) == []
    assert outcomes(db) == ["removed_not_recorded"]


def test_noise_is_ignored(world):
    db, tray = world["db"], world["tray"]
    assert record_tray_event(db, tray, T0, 100.0, 99.0) is None
    assert tray.current_weight_g == 99.0


def test_booking_in_calibrates_item_weight(world):
    from medverify.db import utcnow

    db, tray = world["db"], world["tray"]
    from medverify.labels import PharmacyLabel
    from medverify.register import book_in

    item = book_in(db, world["home"], PharmacyLabel("NEW", "Eye drops", None, "R-1", None, [], None),
                   world["carer"])
    assert item.awaiting_weight
    ev = record_tray_event(db, tray, utcnow(), 50.0, 59.4)
    assert ev.note == "booking_in" and ev.reconciled
    assert item.unit_weight_g == 9.4 and not item.awaiting_weight


def test_identifies_which_item_by_weight(world):
    db, tray = world["db"], world["tray"]
    add_item(world, code="A", name="Insulin pen", weight=24.0)
    drops = add_item(world, code="B", name="Latanoprost eye drops", weight=9.5)
    record_tray_event(db, tray, T0, 100.0, 90.6)
    recs = reconcile_home(db, world["home"], T0 + timedelta(hours=1))
    assert recs[0].item_id == drops.id
