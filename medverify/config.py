"""Runtime configuration, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv("MEDVERIFY_DATABASE_URL", "sqlite:///./medverify.db"))
    # Seed demo organisation, homes, users and example data on start-up.
    demo: bool = field(default_factory=lambda: _bool("MEDVERIFY_DEMO", False))
    # Run the background worker (reconciliation, sensor watchdog, excursion alerts).
    worker: bool = field(default_factory=lambda: _bool("MEDVERIFY_WORKER", True))
    worker_interval_s: int = field(default_factory=lambda: int(os.getenv("MEDVERIFY_WORKER_INTERVAL", "60")))
    token_ttl_hours: int = field(default_factory=lambda: int(os.getenv("MEDVERIFY_TOKEN_TTL_HOURS", "12")))
    # Optional SMS delivery through Twilio. Without these, texts are kept in the outbox only.
    twilio_sid: str | None = field(default_factory=lambda: os.getenv("TWILIO_ACCOUNT_SID"))
    twilio_token: str | None = field(default_factory=lambda: os.getenv("TWILIO_AUTH_TOKEN"))
    twilio_from: str | None = field(default_factory=lambda: os.getenv("TWILIO_FROM_NUMBER"))


settings = Settings()
