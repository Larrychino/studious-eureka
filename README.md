# MedVerify

A low-cost safety layer for medicines in care homes and supported living. It physically checks what leaves and
returns to a medicines fridge, compares that with what staff recorded, and tells staff exactly what to do when
something goes wrong.

This repository is the **pilot version** described in the business plan (Phase 2–3): the cloud service, the staff
phone app, the manager/group dashboard, the weight-tray firmware and a device simulator.

| Plan feature | Where it lives |
|---|---|
| Medicine-aware fridge (what is in each fridge, and whose) | `medverify/register.py`, `labels.py` |
| Excursion decision engine (use / new expiry / discard / quarantine) | `medverify/decision_engine.py`, `temperature.py` |
| Delivery handover check (2–8 °C journey, green/red at the door) | `medverify/delivery.py` |
| Physical stock verification (weight trays vs records) | `medverify/reconciliation.py`, `firmware/weight_tray/` |
| Alerts to the senior on shift (app + optional SMS) | `medverify/alerts.py`, `notify.py` |
| CQC-ready audit log (timed, attributable, tamper-evident) | `medverify/audit.py` |
| Connections: eMAR (CSV import + adapter interface), pharmacy re-orders | `medverify/integrations/` |
| Paper to Digital: simple digital MAR chart | `medverify/rounds.py` |
| Multi-home dashboard, pilot metrics, pricing | `medverify/api/admin.py`, `pricing.py` |
| Phase 1: 30-interview discovery tracker with go-signals | `medverify/discovery.py` |

## Run it

```bash
pip install -r requirements-dev.txt
python -m medverify serve --demo          # open http://127.0.0.1:8000
```

Demo accounts (password `medverify-demo`):

| User | Role | Try |
|---|---|---|
| `carer1` | Carer, Oak House | *Now* (alerts + round); *Scan* → book in `MV1\|OAK-7777\|Insulin lispro pen\|3 ml\|R-001\|4 units\|08:00\|2030-01-01\|`; *Scan* → delivery `BOX-001` (accept) / `BOX-002` (refuse) |
| `senior1` | Senior carer | Resolve the stock mismatch; *Fridges* → excursion instructions and quarantine answer |
| `manager1` | Home manager | *Audit* → export CSV / verify chain; *Settings* → staff, devices, label maker |
| `group1` | Care group | *Dashboard* across both homes |
| `pharm1` | Pharmacist adviser | *Stability rules* → create and approve |
| `founder` | Admin | *Discovery* → interview scoring and go-signals |

Live devices: with the server running, `python simulator/simulate.py --warm-fridge` or `--tray-removal`.

Tests: `python -m pytest` (67 tests covering reconciliation outcomes, decision-engine safety rules, timezone
handling, delivery checks, audit tamper detection, pricing, permissions and the full API).

## How a medicine moves through the system

1. **Delivery**: the pharmacy dispatches a box with a temperature tag. At the door the carer scans the box and gets
   ACCEPT or REFUSE. A refusal notifies the pharmacy and raises a replacement request. A journey that cannot be
   proven (no data, or gaps) is refused.
2. **Booking in**: the carer scans the pharmacy label, and can also scan the pack's GS1 DataMatrix to check GTIN,
   batch and expiry. The item goes to the resident's tray, and the tray's next weight increase calibrates it.
3. **Round**: the tray reports each removal and return. The carer records given, refused or spoilt in the app or
   in their eMAR.
4. **Reconciliation** (every minute): removals and returns are matched to records within the home's window
   (default 30 min).
5. **Mismatch**: removed but not recorded, used but not recorded, recorded but never removed, or refused but not
   put back. Each one alerts the senior on shift (app + text).
6. **Return**: an item put back at the same weight is logged as returned unused.
7. **Fridge problem**: when the fridge leaves 2–8 °C, every item is checked against approved stability rules.
   Staff get one instruction per item, and discards create replacement requests.
8. **Inspection**: every event goes into the hash-chained audit log, which can be exported as CSV.

## Safety design (decision engine)

- Only rules with status *approved*, a **named pharmacist** (with registration number) and a **published source**
  are ever applied. Any edit sends a rule back to draft.
- If there is no rule, a gap in the temperature record, or any other doubt, the instruction is
  **QUARANTINE and ask the pharmacist**.
- Exposure is time-weighted, adds up per item across excursions, and is estimated conservatively: the time since
  the last good reading counts as out of range.
- Items waiting to be discarded or reviewed cannot be recorded as given. Expired items cannot be given.
- Short door-opening blips do not page staff (home setting, default 10 min) but are still assessed. Freezing
  alerts immediately. Out-of-range data that arrives late from a sensor that was offline still raises an alert.

The demo's example rules are labelled **EXAMPLE – not clinically approved**. Before any pilot, replace them with
rules written from each product's SmPC and approved by your pharmacist adviser.

## Architecture

- Python 3.10+, FastAPI, SQLAlchemy 2. SQLite for a pilot; set `MEDVERIFY_DATABASE_URL` to PostgreSQL for
  production (the audit log takes an advisory lock there so the hash chain stays linear).
- Frontend: one installable web app (`medverify/static/`), no build step, phone-first. Camera scanning uses the
  browser's BarcodeDetector (Chrome on Android). Other phones use typed entry or a Bluetooth scanner.
- Devices post batches with `X-Device-Id`/`X-Device-Key`. Keys are stored hashed and shown once at registration.
- Residents are stored by internal ID only. Names are rejected unless the home's data agreement allows them.
- Dose times and alert times use the home's local time (default Europe/London, BST-aware).

Configuration (environment variables): `MEDVERIFY_DATABASE_URL`, `MEDVERIFY_DEMO`, `MEDVERIFY_WORKER`,
`MEDVERIFY_WORKER_INTERVAL`, `MEDVERIFY_TOKEN_TTL_HOURS`. Optionally set `TWILIO_ACCOUNT_SID`,
`TWILIO_AUTH_TOKEN` and `TWILIO_FROM_NUMBER` to send real text messages; without them, texts stay in the outbox table.

See `docs/pilot-checklist.md` for what must be settled before the first home, and `docs/api.md` for the API.
