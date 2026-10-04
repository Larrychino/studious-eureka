from datetime import timedelta

from sqlalchemy import update

from medverify import audit
from medverify.delivery import evaluate_journey
from medverify.discovery import summarise
from medverify.models import AuditEntry, Interview
from medverify.pricing import home_quote, homes_needed_for

from .conftest import T0


def journey(temps, step=5):
    return [(T0 + timedelta(minutes=step * i), t) for i, t in enumerate(temps)]


def test_journey_green():
    r = evaluate_journey(journey([4, 5, 6, 5]), T0, T0 + timedelta(minutes=16))
    assert r.ok


def test_journey_red_when_warm_cold_or_unverifiable():
    end = T0 + timedelta(minutes=16)
    assert not evaluate_journey(journey([4, 9.1, 5, 5]), T0, end).ok
    assert not evaluate_journey(journey([4, 1.5, 5, 5]), T0, end).ok
    assert not evaluate_journey([], T0, end).ok
    gap = evaluate_journey([(T0, 5), (T0 + timedelta(minutes=50), 5)], T0, T0 + timedelta(minutes=51))
    assert not gap.ok and "gap" in gap.reason


def test_audit_chain_detects_tampering(db):
    for i in range(5):
        audit.record(db, "test.event", details={"i": i})
    db.commit()
    assert audit.verify_chain(db) == (True, None)
    db.execute(update(AuditEntry).where(AuditEntry.id == 3).values(actor="someone else"))
    db.commit()
    db.expire_all()
    ok, bad = audit.verify_chain(db)
    assert not ok and bad == 3


def test_pricing_matches_plan_example():
    q = home_quote(fridges=2, trays=10)
    assert q.monthly_gbp == 70 and q.annual_gbp == 840  # plan: £70 a month or £840 a year
    assert homes_needed_for(100_000, 70) == 120  # plan: roughly 120 homes for £100k
    assert home_quote(2, 10, hardware_cost_gbp=400).hardware_payback_ok
    assert not home_quote(2, 10, hardware_cost_gbp=500).hardware_payback_ok
    assert home_quote(1, 0, paper_to_digital=True).monthly_gbp == 49


def iv(group, stock=0, fridge=0, delivery=0, paper=None):
    from datetime import date

    return Interview(group=group, interviewee_label="x", held_on=date(2026, 1, 1), score_stock_mismatch=stock,
                     score_fridge_excursion=fridge, score_delivery_temperature=delivery, still_on_paper=paper)


def test_discovery_go_signals_and_headline():
    data = [iv("manager", stock=3, fridge=2, paper=True) for _ in range(10)]
    data += [iv("pharmacist", delivery=3) for _ in range(3)]
    s = summarise(data)
    assert s["signals"]["stock_mismatch"]["met"]
    assert s["signals"]["fridge_excursion"]["met"]
    assert s["signals"]["delivery_temperature"]["met"]
    assert s["signals"]["paper_charts"]["met"]
    assert s["headline_problem"] == "stock_mismatch"
    assert s["gate_validate_met"]
