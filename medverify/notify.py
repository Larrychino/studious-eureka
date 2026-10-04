"""Outbound notifications: in-app, SMS (optional Twilio) and email outbox.

Every message is written to the Notification outbox first so nothing is lost
if a provider is down, and so the audit trail shows who was told what, when.
"""

from __future__ import annotations

import logging

import httpx
from sqlalchemy.orm import Session

from .config import settings
from .db import utcnow
from .models import Notification

log = logging.getLogger("medverify.notify")


def _send_sms(to: str, body: str) -> None:
    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_sid}/Messages.json"
    resp = httpx.post(url, data={"To": to, "From": settings.twilio_from, "Body": body[:1500]},
                      auth=(settings.twilio_sid, settings.twilio_token), timeout=10)
    resp.raise_for_status()


def queue(db: Session, channel: str, recipient: str, body: str, alert_id: int | None = None) -> Notification:
    note = Notification(channel=channel, recipient=recipient, body=body, alert_id=alert_id)
    db.add(note)
    db.flush()
    if channel == "app":
        # Delivered by the staff app polling /api/alerts; mark as sent immediately.
        note.status, note.sent_at = "sent", utcnow()
    elif channel == "sms" and settings.twilio_sid and settings.twilio_token and settings.twilio_from:
        try:
            _send_sms(recipient, body)
            note.status, note.sent_at = "sent", utcnow()
        except Exception as exc:  # provider failure must never break the alert itself
            note.status, note.error = "failed", str(exc)
            log.warning("SMS to %s failed: %s", recipient, exc)
    else:
        log.info("[%s -> %s] %s", channel, recipient, body)
    return note
