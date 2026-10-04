from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from medverify.app import create_app
from medverify.config import Settings
from medverify.db import Database
from medverify.labels import PharmacyLabel
from medverify.models import Fridge, Home, Organisation, Pharmacy, StabilityRule, Tray, User
from medverify.register import book_in, get_or_create_resident
from medverify.security import hash_password

T0 = datetime(2026, 3, 2, 9, 0)  # winter: UK local time == UTC


@pytest.fixture()
def db():
    database = Database("sqlite://")
    database.create_all()
    session = database.session()
    yield session
    session.close()


@pytest.fixture()
def world(db):
    """A minimal home: one fridge, one resident with a tray, a carer and a senior on shift."""
    org = Organisation(name="Org")
    pharmacy = Pharmacy(name="Pharm", email="p@example.com")
    db.add_all([org, pharmacy])
    db.flush()
    home = Home(organisation_id=org.id, pharmacy_id=pharmacy.id, name="Home A")
    db.add(home)
    db.flush()
    carer = User(username="c", display_name="Carer", role="carer", home_id=home.id, organisation_id=org.id,
                 password_hash=hash_password("password1"), on_shift=True)
    senior = User(username="s", display_name="Senior", role="senior", home_id=home.id, organisation_id=org.id,
                  password_hash=hash_password("password1"), on_shift=True, phone="+447700900000")
    fridge = Fridge(home_id=home.id, name="Fridge 1")
    db.add_all([carer, senior, fridge])
    db.flush()
    resident = get_or_create_resident(db, home, "R-1")
    tray = Tray(home_id=home.id, fridge_id=fridge.id, resident_id=resident.id, label="Tray R-1", current_weight_g=0)
    db.add(tray)
    db.flush()
    return {"db": db, "org": org, "home": home, "carer": carer, "senior": senior, "fridge": fridge,
            "resident": resident, "tray": tray, "pharmacy": pharmacy}


def add_item(world, code="L1", name="Insulin aspart pen", weight=24.0, expiry=None, times=("08:00",)):
    label = PharmacyLabel(code, name, "3 ml", world["resident"].ref, "as directed", list(times),
                          expiry or date(2030, 1, 1))
    item = book_in(world["db"], world["home"], label, world["carer"], unit_weight_g=weight)
    item.booked_in_at = T0 - timedelta(days=1)
    return item


def approved_rule(db, **kw):
    defaults = dict(name="Insulin", match_name="insulin", min_allowed_c=0.5, max_allowed_c=30.0,
                    max_minutes_out=600, new_expiry_days=28, source_reference="SmPC 6.4", status="approved",
                    approved_by_name="Pharmacist X")
    defaults.update(kw)
    rule = StabilityRule(**defaults)
    db.add(rule)
    db.flush()
    return rule


@pytest.fixture()
def client():
    app = create_app(Settings(database_url="sqlite://", demo=True, worker=False))
    with TestClient(app) as c:
        yield c


def login(client, username, password="medverify-demo"):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}
