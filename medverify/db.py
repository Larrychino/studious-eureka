"""Database engine and session handling."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    """Naive UTC timestamp. All times in the database are UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


DEFAULT_TZ = "Europe/London"


def local(value: datetime, tz: str | None = None) -> datetime:
    """Convert a naive UTC timestamp to the home's local wall-clock time (for messages)."""
    from zoneinfo import ZoneInfo

    return value.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(tz or DEFAULT_TZ)).replace(tzinfo=None)


def local_to_utc(value: datetime, tz: str | None = None) -> datetime:
    """Convert a naive local wall-clock time in the home's zone to naive UTC."""
    from zoneinfo import ZoneInfo

    return value.replace(tzinfo=ZoneInfo(tz or DEFAULT_TZ)).astimezone(timezone.utc).replace(tzinfo=None)


def to_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def make_engine(url: str):
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if url in ("sqlite://", "sqlite:///:memory:"):
            from sqlalchemy.pool import StaticPool

            kwargs["poolclass"] = StaticPool
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


class Database:
    def __init__(self, url: str):
        self.engine = make_engine(url)
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)

    def create_all(self) -> None:
        from . import models  # noqa: F401  (register tables)

        Base.metadata.create_all(self.engine)

    def session(self) -> Session:
        return self.SessionLocal()

    def session_scope(self) -> Iterator[Session]:
        db = self.SessionLocal()
        try:
            yield db
        finally:
            db.close()
