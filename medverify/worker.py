"""Background jobs: run every minute by the app (or by cron via `python -m medverify tick`)."""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from .integrations.pharmacy import send_open_requests
from .reconciliation import reconcile_all
from .temperature import watchdog

log = logging.getLogger("medverify.worker")


def tick(db: Session, now: datetime | None = None) -> dict:
    watchdog(db, now)
    records = reconcile_all(db, now)
    sent = send_open_requests(db)
    db.commit()
    if records or sent:
        log.info("worker: %d reconciliation records, %d replacement requests sent", records, sent)
    return {"reconciliation_records": records, "replacements_sent": sent}
