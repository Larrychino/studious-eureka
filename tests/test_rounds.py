from datetime import date, datetime, timedelta

from medverify.rounds import due_now, mar_chart
from medverify.rounds import record_administration

from .conftest import add_item


def test_dose_times_are_uk_local_time_in_summer(world):
    db, home = world["db"], world["home"]
    add_item(world, times=("08:00",))
    # 1 July 07:00 UTC == 08:00 BST
    rows = due_now(db, home, datetime(2026, 7, 1, 7, 0), window_min=30)
    assert len(rows) == 1 and rows[0]["due_local"] == "08:00"
    assert rows[0]["due"].startswith("2026-07-01T07:00")
    assert due_now(db, home, datetime(2026, 7, 1, 8, 0), window_min=30) == []


def test_due_around_midnight(world):
    db, home = world["db"], world["home"]
    add_item(world, times=("23:45",))
    rows = due_now(db, home, datetime(2026, 1, 2, 0, 10), window_min=60)
    assert len(rows) == 1


def test_recorded_dose_marked(world):
    db, home = world["db"], world["home"]
    item = add_item(world, times=("08:00",))
    record_administration(db, item, "given", user=world["carer"], ts=datetime(2026, 1, 5, 8, 5))
    rows = due_now(db, home, datetime(2026, 1, 5, 8, 10))
    assert rows[0]["recorded"] == "given"


def test_mar_chart_places_records_in_local_days(world):
    db, home = world["db"], world["home"]
    item = add_item(world, times=("08:00", "20:00"))
    record_administration(db, item, "given", user=world["carer"], ts=datetime(2026, 7, 1, 7, 2))   # 08:02 BST
    record_administration(db, item, "refused", user=world["carer"], ts=datetime(2026, 7, 1, 19, 0))  # 20:00 BST
    chart = mar_chart(db, home, world["resident"], date(2026, 7, 1), days=2)
    morning = next(r for r in chart["rows"] if r["time"] == "08:00")
    evening = next(r for r in chart["rows"] if r["time"] == "20:00")
    assert morning["cells"][0][0]["outcome"] == "given" and morning["cells"][0][0]["time"] == "08:02"
    assert evening["cells"][0][0]["outcome"] == "refused"
    assert morning["cells"][1] == []


def test_cannot_give_expired(world):
    import pytest

    from medverify.rounds import AdministrationError

    item = add_item(world, expiry=date(2026, 1, 1))
    with pytest.raises(AdministrationError):
        record_administration(world["db"], item, "given", user=world["carer"], ts=datetime(2026, 1, 2, 8, 0))
