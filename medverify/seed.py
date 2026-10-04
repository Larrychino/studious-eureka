"""Demo data: one care group, two homes, a pharmacy, staff, devices, stock and history.

Everything here is fictional. The stability rules are EXAMPLES marked as such
and are NOT clinically approved: real rules must come from the product's
published stability data (SmPC) and be approved by a named pharmacist.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import utcnow
from .delivery import add_journey_readings, dispatch
from .labels import PharmacyLabel
from .models import (
    Device,
    DeliveryBox,
    Fridge,
    Home,
    Organisation,
    Pharmacy,
    StabilityRule,
    Tray,
    User,
)
from .reconciliation import reconcile_home, record_tray_event
from .register import book_in, get_or_create_resident
from .rounds import record_administration
from .security import hash_password, sha256
from .temperature import ingest_reading

DEMO_PASSWORD = "medverify-demo"
DEMO_SOURCE = ("EXAMPLE ONLY - not clinical advice. Replace with the product's SmPC section 6.3/6.4 "
               "(emc.medicines.org.uk) and have your pharmacist adviser approve it.")
DEMO_APPROVER = "DEMO ONLY - not clinically approved"

# Device keys used by simulator/simulate.py in demo mode only.
DEMO_DEVICES = {
    "SENSOR-OAK-1": ("fridge_sensor", "demo-sensor-oak-1"),
    "SENSOR-OAK-2": ("fridge_sensor", "demo-sensor-oak-2"),
    "SENSOR-BIRCH-1": ("fridge_sensor", "demo-sensor-birch-1"),
    "TRAY-OAK-R001": ("weight_tray", "demo-tray-oak-r001"),
    "TRAY-OAK-R002": ("weight_tray", "demo-tray-oak-r002"),
    "TRAY-OAK-R003": ("weight_tray", "demo-tray-oak-r003"),
    "GATEWAY-PHARM-1": ("gateway", "demo-gateway-pharm-1"),
}


def seed_demo(db: Session) -> None:
    if db.scalar(select(Organisation).where(Organisation.name == "Demo Care Group")):
        return
    now = utcnow().replace(second=0, microsecond=0)

    org = Organisation(name="Demo Care Group")
    pharmacy = Pharmacy(name="Demo Community Pharmacy", email="orders@pharmacy.example", phone="+440000000000")
    db.add_all([org, pharmacy])
    db.flush()
    oak = Home(organisation_id=org.id, pharmacy_id=pharmacy.id, name="Oak House (demo)", uses_digital_mar=True)
    birch = Home(organisation_id=org.id, pharmacy_id=pharmacy.id, name="Birch Lodge (demo)",
                 uses_digital_mar=False, emar_system="External eMAR (CSV export)")
    db.add_all([oak, birch])
    db.flush()

    pw = hash_password(DEMO_PASSWORD)
    users = {
        "carer1": User(username="carer1", display_name="Amina (carer)", role="carer", home_id=oak.id,
                       organisation_id=org.id, on_shift=True),
        "senior1": User(username="senior1", display_name="Joe (senior carer)", role="senior", home_id=oak.id,
                        organisation_id=org.id, on_shift=True),
        "manager1": User(username="manager1", display_name="Priya (home manager)", role="manager",
                         home_id=oak.id, organisation_id=org.id),
        "manager2": User(username="manager2", display_name="Tom (home manager)", role="manager",
                         home_id=birch.id, organisation_id=org.id),
        "group1": User(username="group1", display_name="Grace (group operations)", role="group_admin",
                       organisation_id=org.id),
        "pharm1": User(username="pharm1", display_name="Dr Sam (pharmacist adviser)", role="pharmacist",
                       organisation_id=org.id, registration_number="DEMO-0000000"),
        "founder": User(username="founder", display_name="Founder", role="admin"),
    }
    for u in users.values():
        u.password_hash = pw
        db.add(u)
    db.flush()

    oak_f1 = Fridge(home_id=oak.id, name="Oak medicines fridge")
    oak_f2 = Fridge(home_id=oak.id, name="Oak nursing-unit fridge")
    birch_f1 = Fridge(home_id=birch.id, name="Birch medicines fridge")
    db.add_all([oak_f1, oak_f2, birch_f1])
    db.flush()

    devices = {}
    for ext, (kind, key) in DEMO_DEVICES.items():
        home_id = None if kind == "gateway" else (birch.id if "BIRCH" in ext else oak.id)
        d = Device(kind=kind, home_id=home_id, external_id=ext, api_key_hash=sha256(key))
        db.add(d)
        db.flush()
        devices[ext] = d
    oak_f1.sensor_device_id = devices["SENSOR-OAK-1"].id
    oak_f2.sensor_device_id = devices["SENSOR-OAK-2"].id
    birch_f1.sensor_device_id = devices["SENSOR-BIRCH-1"].id

    # Residents by internal reference only (Oak's data agreement does not allow names).
    residents = [get_or_create_resident(db, oak, ref) for ref in ("R-001", "R-002", "R-003")]
    trays = []
    for i, r in enumerate(residents, start=1):
        fridge = oak_f1 if i < 3 else oak_f2
        t = Tray(home_id=oak.id, fridge_id=fridge.id, resident_id=r.id, label=f"Tray {r.ref}",
                 device_id=devices[f"TRAY-OAK-R00{i}"].id, current_weight_g=0.0)
        db.add(t)
        trays.append(t)
    db.flush()

    # Example stability rules (clearly marked; see module docstring).
    db.add_all([
        StabilityRule(name="Insulin pens/vials (EXAMPLE)", match_name="insulin", min_allowed_c=0.5,
                      max_allowed_c=30.0, max_minutes_out=28 * 24 * 60, new_expiry_days=28,
                      source_reference=DEMO_SOURCE, status="approved", approved_by_name=DEMO_APPROVER,
                      approved_at=now, notes="Do not freeze. Typical SmPCs allow room-temperature use for a limited period."),
        StabilityRule(name="Latanoprost eye drops (EXAMPLE)", match_name="latanoprost", min_allowed_c=0.5,
                      max_allowed_c=25.0, max_minutes_out=28 * 24 * 60, new_expiry_days=28,
                      source_reference=DEMO_SOURCE, status="approved", approved_by_name=DEMO_APPROVER,
                      approved_at=now),
        StabilityRule(name="Vaccines (EXAMPLE draft, not approved)", match_name="vaccine", min_allowed_c=2.0,
                      max_allowed_c=8.0, max_minutes_out=0, status="draft",
                      notes="Draft: the decision engine ignores drafts, so vaccines are quarantined."),
    ])

    carer = users["carer1"]
    expiry = date.today() + timedelta(days=120)
    stock = [
        PharmacyLabel("OAK-0001", "Insulin aspart 100 units/ml pen", "3 ml", "R-001", "8 units before breakfast",
                      ["08:00"], expiry),
        PharmacyLabel("OAK-0002", "Latanoprost 50 micrograms/ml eye drops", "2.5 ml", "R-001",
                      "One drop in each eye at night", ["20:00"], expiry),
        PharmacyLabel("OAK-0003", "Insulin glargine 100 units/ml pen", "3 ml", "R-002", "14 units in the evening",
                      ["18:00"], expiry),
        PharmacyLabel("OAK-0004", "Amoxicillin 250mg/5ml oral suspension", "100 ml", "R-002",
                      "5 ml three times a day", ["08:00", "14:00", "20:00"], date.today() + timedelta(days=5)),
        PharmacyLabel("OAK-0005", "Influenza vaccine (demo)", "0.5 ml", "R-003", "Single dose by nurse", [], expiry),
        PharmacyLabel("OAK-0006", "Insulin aspart 100 units/ml pen", "3 ml", "R-003", "6 units with lunch",
                      ["12:30"], expiry),
    ]
    weights = [24.0, 9.5, 24.5, 140.0, 12.0, 24.0]
    items = []
    for label, grams in zip(stock, weights):
        item = book_in(db, oak, label, carer, unit_weight_g=grams)
        item.booked_in_at = now - timedelta(days=2)
        items.append(item)
        tray = item.tray
        tray.current_weight_g = (tray.current_weight_g or 0) + grams

    # 24 hours of fridge readings every 10 minutes. Oak nursing-unit fridge has a
    # warm spell (door left ajar) 5-4 hours ago to show the decision engine.
    start = now - timedelta(hours=24)
    for step in range(24 * 6 + 1):
        ts = start + timedelta(minutes=10 * step)
        wobble = 0.6 * math.sin(step / 7)
        ingest_reading(db, oak_f1, ts, round(4.6 + wobble, 1), False)
        hours_ago = (now - ts).total_seconds() / 3600
        warm = 4.0 <= hours_ago <= 5.0
        ingest_reading(db, oak_f2, ts, round((11.5 if warm else 5.1) + wobble, 1), warm)
        ingest_reading(db, birch_f1, ts, round(5.4 - wobble, 1), False)

    # This morning's round on Oak: R-001's insulin removed, given, returned lighter.
    t0 = now - timedelta(hours=3)
    tray1 = trays[0]
    w = tray1.current_weight_g
    record_tray_event(db, tray1, t0, w, w - 24.0)
    record_administration(db, items[0], "given", user=carer, ts=t0 + timedelta(minutes=2))
    record_tray_event(db, tray1, t0 + timedelta(minutes=4), w - 24.0, w - 0.8)
    # Eye drops taken out and put back unused.
    w = tray1.current_weight_g
    record_tray_event(db, tray1, t0 + timedelta(minutes=10), w, w - 9.5)
    record_tray_event(db, tray1, t0 + timedelta(minutes=11), w - 9.5, w)
    # R-002's amoxicillin taken out and not recorded: a mismatch the senior must check.
    tray2 = trays[1]
    w = tray2.current_weight_g
    record_tray_event(db, tray2, t0 + timedelta(minutes=20), w, w - 140.0)
    reconcile_home(db, oak, now)

    # Deliveries: one good journey, one that got warm in the van.
    box1 = DeliveryBox(pharmacy_id=pharmacy.id, code="BOX-001", tag_external_id="TAG-001")
    box2 = DeliveryBox(pharmacy_id=pharmacy.id, code="BOX-002", tag_external_id="TAG-002")
    db.add_all([box1, box2])
    db.flush()
    d1 = dispatch(db, box1, oak.id, users["pharm1"], now - timedelta(minutes=70), "2 insulin pens")
    d2 = dispatch(db, box2, oak.id, users["pharm1"], now - timedelta(minutes=70), "1 eye drops")
    good = [(now - timedelta(minutes=70) + timedelta(minutes=5 * k), 4.5 + 0.3 * math.sin(k)) for k in range(15)]
    bad = [(ts, t + (6.0 if 6 <= k <= 9 else 0)) for k, (ts, t) in enumerate(good)]
    add_journey_readings(db, d1, good)
    add_journey_readings(db, d2, bad)
    db.commit()
