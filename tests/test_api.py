from datetime import date, datetime, timedelta, timezone

from medverify.db import utcnow

from .conftest import login


def test_health_and_static(client):
    assert client.get("/api/health").json()["ok"]
    assert "MedVerify" in client.get("/").text


def test_login_rejects_wrong_password(client):
    assert client.post("/api/auth/login", json={"username": "carer1", "password": "nope"}).status_code == 401


def test_requires_auth(client):
    assert client.get("/api/homes").status_code == 401


def test_home_isolation(client):
    h = login(client, "manager2")  # Birch manager
    assert [x["name"] for x in client.get("/api/homes", headers=h).json()] == ["Birch Lodge (demo)"]
    assert client.get("/api/homes/1/items", headers=h).status_code == 404
    g = login(client, "group1")
    assert len(client.get("/api/homes", headers=g).json()) == 2


def test_demo_has_mismatch_excursion_and_alerts(client):
    h = login(client, "senior1")
    recon = client.get("/api/homes/1/reconciliation?mismatch_only=true", headers=h).json()
    assert [r["outcome"] for r in recon] == ["removed_not_recorded"]
    ex = client.get("/api/homes/1/excursions", headers=h).json()
    detail = client.get(f"/api/excursions/{ex[0]['id']}", headers=h).json()
    assert {d["decision"] for d in detail["decisions"]} == {"quarantine", "use_new_expiry"}


def test_carer_cannot_resolve_mismatch_but_senior_can(client):
    c, s = login(client, "carer1"), login(client, "senior1")
    alert = next(a for a in client.get("/api/homes/1/alerts", headers=c).json() if a["type"] == "stock_mismatch")
    assert client.post(f"/api/alerts/{alert['id']}/resolve", json={"action_taken": "found it"}, headers=c).status_code == 403
    r = client.post(f"/api/alerts/{alert['id']}/resolve", json={"action_taken": "Dose was given, record added late"}, headers=s)
    assert r.status_code == 200 and r.json()["resolved_at"]


def test_book_in_and_give(client):
    h = login(client, "carer1")
    label = "MV1|OAK-9001|Insulin lispro pen|3 ml|R-002|4 units with meals|08:00;12:00|2030-01-01|"
    r = client.post("/api/homes/1/items/book-in", json={"label_text": label}, headers=h)
    assert r.status_code == 200, r.text
    item = r.json()
    assert item["tray"] == "Tray R-002" and item["awaiting_weight"]
    assert client.post("/api/homes/1/items/book-in", json={"label_text": label}, headers=h).status_code == 422
    r = client.post(f"/api/items/{item['id']}/administrations", json={"outcome": "given"}, headers=h)
    assert r.status_code == 200


def test_pack_must_match_label(client):
    h = login(client, "carer1")
    label = "MV1|OAK-9002|Eye drops|5 ml|R-001|||2030-01-01|09506000134352"
    r = client.post("/api/homes/1/items/book-in",
                    json={"label_text": label, "pack_text": "(01)05012345678900(17)301231(10)X"}, headers=h)
    assert r.status_code == 422 and "does not match" in r.json()["detail"]


def test_cannot_give_quarantined_item(client):
    h = login(client, "carer1")
    q = client.get("/api/homes/1/items?status=quarantined", headers=h).json()
    r = client.post(f"/api/items/{q[0]['id']}/administrations", json={"outcome": "given"}, headers=h)
    assert r.status_code == 409 and "quarantined" in r.json()["detail"]


def test_resident_names_blocked_without_agreement(client):
    h = login(client, "manager1")
    r = client.post("/api/homes/1/residents", json={"ref": "R-010", "display_name": "Jane Doe"}, headers=h)
    assert r.status_code == 422


def test_rules_need_pharmacist_and_source(client):
    p, m = login(client, "pharm1"), login(client, "manager1")
    body = {"name": "Test", "match_name": "testmed", "max_minutes_out": 60}
    assert client.post("/api/rules", json=body, headers=m).status_code == 403
    rule = client.post("/api/rules", json=body, headers=p).json()
    assert client.post(f"/api/rules/{rule['id']}/approve", headers=p).status_code == 422  # no source
    body["source_reference"] = "SmPC 6.4 (2025)"
    client.patch(f"/api/rules/{rule['id']}", json=body, headers=p)
    r = client.post(f"/api/rules/{rule['id']}/approve", headers=p).json()
    assert r["usable"] and "DEMO-0000000" in r["approved_by_name"]


def test_device_ingestion_and_excursion(client):
    hdr = {"X-Device-Id": "SENSOR-OAK-1", "X-Device-Key": "demo-sensor-oak-1"}
    now = datetime.now(timezone.utc)
    readings = [{"ts": (now + timedelta(seconds=5 * i)).isoformat(), "temp_c": t}
                for i, t in enumerate([9.5, 10.0, 10.2, 10.1, 5.0])]
    r = client.post("/api/devices/readings", json={"readings": readings}, headers=hdr)
    assert r.status_code == 200
    bad = client.post("/api/devices/readings", json={"readings": [{"temp_c": 5}]},
                      headers={"X-Device-Id": "SENSOR-OAK-1", "X-Device-Key": "wrong"})
    assert bad.status_code == 401
    h = login(client, "senior1")
    fridges = {f["name"]: f for f in client.get("/api/homes/1/fridges", headers=h).json()}
    assert fridges["Oak medicines fridge"]["last_temp_c"] == 5.0
    ex = client.get("/api/homes/1/excursions", headers=h).json()
    assert any(e["fridge"] == "Oak medicines fridge" and e["assessed"] for e in ex)


def test_late_out_of_range_data_still_alerts(client):
    hdr = {"X-Device-Id": "SENSOR-BIRCH-1", "X-Device-Key": "demo-sensor-birch-1"}
    late = (datetime.now(timezone.utc) - timedelta(hours=3, minutes=7)).isoformat()
    client.post("/api/devices/readings", json={"readings": [{"ts": late, "temp_c": 13.0}]}, headers=hdr)
    alerts = client.get("/api/homes/2/alerts", headers=login(client, "manager2")).json()
    assert any("late data" in a["title"] for a in alerts)


def test_tray_device_and_reconcile(client):
    hdr = {"X-Device-Id": "TRAY-OAK-R003", "X-Device-Key": "demo-tray-oak-r003"}
    ts = (utcnow() - timedelta(hours=2)).isoformat()
    r = client.post("/api/devices/tray-events", json={"events": [{"ts": ts, "weight_before_g": 36, "weight_after_g": 12}]},
                    headers=hdr)
    assert r.json()["stored"] == 1
    h = login(client, "senior1")
    recs = client.post("/api/homes/1/reconcile", headers=h).json()
    assert [x["outcome"] for x in recs] == ["removed_not_recorded"]


def test_delivery_door_check(client):
    h = login(client, "carer1")
    assert client.post("/api/deliveries/check", json={"box_code": "BOX-001"}, headers=h).json()["result"] == "green"
    r = client.post("/api/deliveries/check", json={"box_code": "BOX-002"}, headers=h).json()
    assert r["result"] == "red"
    assert client.post("/api/deliveries/check", json={"box_code": "BOX-002"}, headers=h).status_code == 404


def test_emar_csv_import(client):
    h = login(client, "senior1")
    ts = (utcnow() - timedelta(minutes=5)).isoformat() + "Z"
    csv_text = f"record_id,label_code,timestamp,outcome,carer\nX1,OAK-0003,{ts},Administered,JB\nX2,NOPE,{ts},given,JB\n"
    r = client.post("/api/homes/1/emar/import", content=csv_text, headers={**h, "Content-Type": "text/csv"}).json()
    assert r["imported"] == 1 and len(r["errors"]) == 1
    r = client.post("/api/homes/1/emar/import", content=csv_text, headers={**h, "Content-Type": "text/csv"}).json()
    assert r["duplicates_skipped"] == 1


def test_audit_export_and_verify(client):
    h = login(client, "manager1")
    csv_text = client.get("/api/homes/1/audit.csv", headers=h).text
    assert csv_text.startswith("entry_id,time_utc") and "item.booked_in" in csv_text
    assert client.get("/api/audit/verify", headers=h).json()["intact"]
    assert client.get("/api/homes/1/audit.csv", headers=login(client, "carer1")).status_code == 403


def test_mar_chart(client):
    h = login(client, "senior1")
    residents = client.get("/api/homes/1/residents", headers=h).json()
    r001 = next(r for r in residents if r["ref"] == "R-001")
    mar = client.get(f"/api/homes/1/mar?resident_id={r001['id']}&days=2", headers=h).json()
    insulin = next(r for r in mar["rows"] if r["medicine"].startswith("Insulin"))
    assert any(any(c["outcome"] == "given" for c in cell) for cell in insulin["cells"])


def test_dashboard_and_quote(client):
    d = client.get("/api/dashboard", headers=login(client, "group1")).json()
    assert len(d["homes"]) == 2 and d["totals"]["mismatches_caught"] >= 1
    assert client.get("/api/dashboard", headers=login(client, "carer1")).status_code == 403
    q = client.post("/api/pricing/quote", json={"fridges": 2, "trays": 10}).json()
    assert q["monthly_gbp"] == 70 and q["homes_for_100k"] == 120


def test_discovery_admin_only(client):
    a = login(client, "founder")
    body = {"group": "manager", "interviewee_label": "PM", "held_on": str(date.today()), "score_stock_mismatch": 3,
            "score_fridge_excursion": 2, "score_delivery_temperature": 0, "still_on_paper": True}
    assert client.post("/api/discovery/interviews", json=body, headers=a).status_code == 200
    s = client.get("/api/discovery/summary", headers=a).json()
    assert s["total"] == 1 and s["headline_problem"] == "stock_mismatch"
    assert client.get("/api/discovery/summary", headers=login(client, "manager1")).status_code == 403
