# Before the first pilot home

The software is built. These items are not software, and they must be done first (from the plan's
"Regulation to settle before any pilot" and the Phase 2 checklist).

## Clinical
- [ ] Recruit the pharmacist adviser and create their `pharmacist` account with their GPhC number.
- [ ] Retire every demo rule. Write the first 20 rules (insulin, eye drops, common antibiotics) from each
      product's SmPC (section 6.3/6.4) and have the adviser approve them in the app.
- [ ] Agree the quarantine process: who the senior phones, and how the answer is recorded (Fridges → Quarantined).

## Regulatory and data protection
- [ ] Register the company and pay the ICO data protection fee.
- [ ] Write to the MHRA asking whether the decision engine is software as a medical device; keep the reply.
- [ ] Data processing agreement with each home. Tick "Data agreement allows resident names" only if it does.
- [ ] Complete the NHS Data Security and Protection Toolkit before handling NHS-linked data.
- [ ] Cyber Essentials certification.
- [ ] Public and product liability insurance.

## Technical
- [ ] Host in a UK region with HTTPS (behind a reverse proxy), PostgreSQL, daily encrypted backups.
- [ ] Create the platform admin with `python -m medverify create-admin <name>`. Never run production with `--demo`.
- [ ] Choose a fridge sensor with an open API, and write a small bridge that posts to `/api/devices/readings`.
- [ ] Bench-test one weight tray for two weeks inside a fridge (load cells drift with cold).
- [ ] Get the home's eMAR administration export and test the CSV import with it.

## Pilot measurement (Phase 3)
Track weekly from the Dashboard: mismatches caught, fridge alerts handled, staff time saved. Replace the
time-saved assumptions in `medverify/api/admin.py` with what pilot homes actually measure.
