# API summary

Interactive docs are at `/docs` (OpenAPI) while the server is running. All staff endpoints need
`Authorization: Bearer <token>` from `POST /api/auth/login`.

| Area | Endpoints |
|---|---|
| Auth | `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/me`, `POST /api/me/shift` |
| Homes and set-up | `GET/PATCH /api/homes/{id}`; `GET/POST /api/homes/{id}/fridges`, `/residents`, `/trays`, `/users`; `POST /api/homes/{id}/devices` |
| Scanning and register | `POST /api/labels/parse`, `POST /api/labels/build`, `POST /api/homes/{id}/items/book-in`, `GET /api/homes/{id}/items` |
| Rounds | `GET /api/homes/{id}/round`, `POST /api/items/{id}/administrations`, `GET /api/homes/{id}/mar`, `POST /api/homes/{id}/emar/import` (text/csv) |
| Reconciliation | `POST /api/homes/{id}/reconcile`, `GET /api/homes/{id}/reconciliation`, `GET /api/trays/{id}/events` |
| Fridges | `GET /api/fridges/{id}/readings`, `POST /api/fridges/{id}/manual-reading`, `GET /api/homes/{id}/excursions`, `GET /api/excursions/{id}`, `POST /api/decisions/{id}/acknowledge`, `POST /api/items/{id}/quarantine-resolution` |
| Deliveries | `POST /api/deliveries/dispatch`, `POST /api/deliveries/check`, `GET /api/homes/{id}/deliveries`, `GET /api/homes/{id}/replacements` |
| Alerts | `GET /api/homes/{id}/alerts`, `POST /api/alerts/{id}/seen`, `POST /api/alerts/{id}/resolve` |
| Audit | `GET /api/homes/{id}/audit`, `GET /api/homes/{id}/audit.csv`, `GET /api/audit/verify` |
| Rules | `GET/POST /api/rules`, `PATCH /api/rules/{id}`, `POST /api/rules/{id}/approve`, `POST /api/rules/{id}/retire` |
| Dashboard and pricing | `GET /api/dashboard`, `GET /api/pricing/plans`, `POST /api/pricing/quote`, `GET /api/homes/{id}/quote` |
| Discovery | `GET/POST /api/discovery/interviews`, `PUT/DELETE /api/discovery/interviews/{id}`, `GET /api/discovery/summary` |
| Devices (`X-Device-Id`, `X-Device-Key`) | `POST /api/devices/readings`, `POST /api/devices/tray-events`, `POST /api/devices/delivery-tag` |

## Pharmacy label QR format

```
MV1|<label code>|<medicine>|<strength>|<resident ID>|<dose instructions>|<HH:MM;HH:MM>|<YYYY-MM-DD expiry>|<GTIN>
```

Dose times are in the home's local time (default Europe/London). Pack barcodes use standard GS1 fields: 01 GTIN,
17 expiry, 10 batch, 21 serial.

## eMAR CSV import columns

`record_id, label_code, timestamp (ISO 8601), outcome (given/administered, refused/declined, spoilt/destroyed), carer, notes`
