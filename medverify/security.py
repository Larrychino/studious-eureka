"""Password hashing, session tokens and device API keys (stdlib only)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import utcnow
from .models import AuthToken, Device, User

_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), dklen=32, **_SCRYPT)
    return hmac.compare_digest(digest.hex(), digest_hex)


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def issue_token(db: Session, user: User, ttl_hours: int) -> str:
    token = secrets.token_urlsafe(32)
    db.add(AuthToken(token_hash=sha256(token), user_id=user.id, expires_at=utcnow() + timedelta(hours=ttl_hours)))
    db.commit()
    return token


def user_for_token(db: Session, token: str) -> User | None:
    row = db.scalar(select(AuthToken).where(AuthToken.token_hash == sha256(token)))
    if row is None or row.expires_at < utcnow() or not row.user.active:
        return None
    return row.user


def revoke_token(db: Session, token: str) -> None:
    row = db.scalar(select(AuthToken).where(AuthToken.token_hash == sha256(token)))
    if row is not None:
        db.delete(row)
        db.commit()


def new_device_key() -> str:
    return "mvd_" + secrets.token_urlsafe(24)


def device_for_key(db: Session, external_id: str, api_key: str) -> Device | None:
    device = db.scalar(select(Device).where(Device.external_id == external_id))
    if device is None or not hmac.compare_digest(device.api_key_hash, sha256(api_key)):
        return None
    return device
