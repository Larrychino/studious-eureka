"""ORM models.

The five core records from the plan are MedicineItem, TrayEvent, Administration,
TemperatureReading and Alert. Everything else supports them.

Residents are stored by internal reference only. A display name is accepted
only when the home's data agreement allows it (Home.allow_resident_names).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, utcnow

# ---- Enumerations (stored as strings) -------------------------------------

ROLES = ("carer", "senior", "manager", "group_admin", "pharmacist", "admin")

ITEM_STATUSES = ("in_stock", "quarantined", "discarded", "finished", "returned_to_pharmacy")
ADMIN_OUTCOMES = ("given", "refused", "spoilt")
DECISIONS = ("use", "use_new_expiry", "discard", "quarantine")
RULE_STATUSES = ("draft", "approved", "retired")

ALERT_TYPES = (
    "stock_mismatch",
    "fridge_out_of_range",
    "excursion_decision",
    "sensor_offline",
    "door_open",
    "delivery_refused",
    "quarantine_review",
    "expiry",
)


class Organisation(Base):
    """A care group (or a single independent home's owning company)."""

    __tablename__ = "organisations"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    homes: Mapped[list[Home]] = relationship(back_populates="organisation")


class Pharmacy(Base):
    __tablename__ = "pharmacies"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(50))


class Home(Base):
    __tablename__ = "homes"
    id: Mapped[int] = mapped_column(primary_key=True)
    organisation_id: Mapped[int] = mapped_column(ForeignKey("organisations.id"))
    pharmacy_id: Mapped[int | None] = mapped_column(ForeignKey("pharmacies.id"))
    name: Mapped[str] = mapped_column(String(200))
    cqc_location_id: Mapped[str | None] = mapped_column(String(50))
    # Dose times on labels and times in messages are in the home's local time.
    timezone: Mapped[str] = mapped_column(String(50), default="Europe/London")
    # Data agreement flag: store resident display names in the cloud?
    allow_resident_names: Mapped[bool] = mapped_column(Boolean, default=False)
    # Records kept in our digital MAR chart ("paper swap") rather than an external eMAR.
    uses_digital_mar: Mapped[bool] = mapped_column(Boolean, default=True)
    emar_system: Mapped[str | None] = mapped_column(String(100))
    # Reconciliation: removal and record must happen within this many minutes of each other.
    reconcile_window_min: Mapped[int] = mapped_column(Integer, default=30)
    # Out-of-range readings shorter than this do not page staff (door-opening blips),
    # but are still assessed by the decision engine. Freezing temperatures alert immediately.
    excursion_grace_min: Mapped[int] = mapped_column(Integer, default=10)
    sensor_offline_min: Mapped[int] = mapped_column(Integer, default=60)
    door_open_alert_min: Mapped[int] = mapped_column(Integer, default=5)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    organisation: Mapped[Organisation] = relationship(back_populates="homes")
    pharmacy: Mapped[Pharmacy | None] = relationship()


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    organisation_id: Mapped[int | None] = mapped_column(ForeignKey("organisations.id"))
    home_id: Mapped[int | None] = mapped_column(ForeignKey("homes.id"))
    username: Mapped[str] = mapped_column(String(100), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20))
    password_hash: Mapped[str] = mapped_column(String(300))
    phone: Mapped[str | None] = mapped_column(String(50))
    on_shift: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # For pharmacists: professional registration (GPhC number) recorded on rule approvals.
    registration_number: Mapped[str | None] = mapped_column(String(50))


class AuthToken(Base):
    __tablename__ = "auth_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)

    user: Mapped[User] = relationship()


class Device(Base):
    """A fridge sensor, weight tray or delivery tag that authenticates with an API key."""

    __tablename__ = "devices"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # fridge_sensor | weight_tray | gateway
    home_id: Mapped[int | None] = mapped_column(ForeignKey("homes.id"))
    external_id: Mapped[str] = mapped_column(String(100), unique=True)
    api_key_hash: Mapped[str] = mapped_column(String(64))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    firmware: Mapped[str | None] = mapped_column(String(50))


class Fridge(Base):
    __tablename__ = "fridges"
    id: Mapped[int] = mapped_column(primary_key=True)
    home_id: Mapped[int] = mapped_column(ForeignKey("homes.id"))
    name: Mapped[str] = mapped_column(String(100))
    min_c: Mapped[float] = mapped_column(Float, default=2.0)
    max_c: Mapped[float] = mapped_column(Float, default=8.0)
    sensor_device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id"))
    last_temp_c: Mapped[float | None] = mapped_column(Float)
    last_reading_at: Mapped[datetime | None] = mapped_column(DateTime)
    door_open_since: Mapped[datetime | None] = mapped_column(DateTime)

    home: Mapped[Home] = relationship()


class Resident(Base):
    __tablename__ = "residents"
    __table_args__ = (UniqueConstraint("home_id", "ref"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    home_id: Mapped[int] = mapped_column(ForeignKey("homes.id"))
    ref: Mapped[str] = mapped_column(String(50))  # internal ID linked to the home's own records
    display_name: Mapped[str | None] = mapped_column(String(200))
    room: Mapped[str | None] = mapped_column(String(50))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Tray(Base):
    """A weight-sensing tray. One per resident with fridge medicines."""

    __tablename__ = "trays"
    id: Mapped[int] = mapped_column(primary_key=True)
    home_id: Mapped[int] = mapped_column(ForeignKey("homes.id"))
    fridge_id: Mapped[int] = mapped_column(ForeignKey("fridges.id"))
    resident_id: Mapped[int | None] = mapped_column(ForeignKey("residents.id"))
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id"))
    label: Mapped[str] = mapped_column(String(100))
    current_weight_g: Mapped[float | None] = mapped_column(Float)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime)

    fridge: Mapped[Fridge] = relationship()
    resident: Mapped[Resident | None] = relationship()


class MedicineItem(Base):
    __tablename__ = "medicine_items"
    __table_args__ = (UniqueConstraint("home_id", "label_code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    home_id: Mapped[int] = mapped_column(ForeignKey("homes.id"))
    label_code: Mapped[str] = mapped_column(String(100))  # pharmacy label code
    medicine_name: Mapped[str] = mapped_column(String(200))
    strength: Mapped[str | None] = mapped_column(String(100))
    resident_id: Mapped[int | None] = mapped_column(ForeignKey("residents.id"))
    dose_instructions: Mapped[str | None] = mapped_column(Text)
    dose_times: Mapped[str | None] = mapped_column(String(200))  # "08:00,18:00"
    expiry: Mapped[date | None] = mapped_column(Date)
    # Shortened expiry set by the decision engine or after opening.
    adjusted_expiry: Mapped[date | None] = mapped_column(Date)
    tray_id: Mapped[int | None] = mapped_column(ForeignKey("trays.id"))
    fridge_id: Mapped[int | None] = mapped_column(ForeignKey("fridges.id"))
    gtin: Mapped[str | None] = mapped_column(String(14))
    batch: Mapped[str | None] = mapped_column(String(50))
    serial: Mapped[str | None] = mapped_column(String(50))
    unit_weight_g: Mapped[float | None] = mapped_column(Float)
    awaiting_weight: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(30), default="in_stock")
    # Cumulative time this item has spent outside 2-8 C across all excursions.
    minutes_out_of_range: Mapped[float] = mapped_column(Float, default=0.0)
    booked_in_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    booked_in_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    delivery_id: Mapped[int | None] = mapped_column(ForeignKey("deliveries.id"))

    resident: Mapped[Resident | None] = relationship()
    tray: Mapped[Tray | None] = relationship()
    fridge: Mapped[Fridge | None] = relationship()

    @property
    def effective_expiry(self) -> date | None:
        dates = [d for d in (self.expiry, self.adjusted_expiry) if d is not None]
        return min(dates) if dates else None


class TrayEvent(Base):
    __tablename__ = "tray_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    tray_id: Mapped[int] = mapped_column(ForeignKey("trays.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    weight_before_g: Mapped[float] = mapped_column(Float)
    weight_after_g: Mapped[float] = mapped_column(Float)
    delta_g: Mapped[float] = mapped_column(Float)
    kind: Mapped[str] = mapped_column(String(20))  # removed | returned
    reconciled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    note: Mapped[str | None] = mapped_column(String(100))  # e.g. "booking_in"

    tray: Mapped[Tray] = relationship()


class Administration(Base):
    __tablename__ = "administrations"
    __table_args__ = (UniqueConstraint("source", "external_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("medicine_items.id"), index=True)
    carer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    carer_ref: Mapped[str | None] = mapped_column(String(100))  # for eMAR imports
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    outcome: Mapped[str] = mapped_column(String(20))  # given | refused | spoilt
    source: Mapped[str] = mapped_column(String(30), default="app")  # app | emar_import | emar_api
    external_id: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)
    reconciled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    item: Mapped[MedicineItem] = relationship()


class ReconciliationRecord(Base):
    """Result of comparing what physically happened with what was recorded."""

    __tablename__ = "reconciliation_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    home_id: Mapped[int] = mapped_column(ForeignKey("homes.id"), index=True)
    tray_id: Mapped[int | None] = mapped_column(ForeignKey("trays.id"))
    item_id: Mapped[int | None] = mapped_column(ForeignKey("medicine_items.id"))
    removal_event_id: Mapped[int | None] = mapped_column(ForeignKey("tray_events.id"))
    return_event_id: Mapped[int | None] = mapped_column(ForeignKey("tray_events.id"))
    administration_id: Mapped[int | None] = mapped_column(ForeignKey("administrations.id"))
    outcome: Mapped[str] = mapped_column(String(40))
    mismatch: Mapped[bool] = mapped_column(Boolean, default=False)
    detail: Mapped[str | None] = mapped_column(Text)
    alert_id: Mapped[int | None] = mapped_column(ForeignKey("alerts.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TemperatureReading(Base):
    __tablename__ = "temperature_readings"
    id: Mapped[int] = mapped_column(primary_key=True)
    fridge_id: Mapped[int | None] = mapped_column(ForeignKey("fridges.id"), index=True)
    delivery_id: Mapped[int | None] = mapped_column(ForeignKey("deliveries.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    temp_c: Mapped[float] = mapped_column(Float)
    door_open: Mapped[bool | None] = mapped_column(Boolean)
    source: Mapped[str] = mapped_column(String(30), default="sensor")  # sensor | manual | tag


class Excursion(Base):
    __tablename__ = "excursions"
    id: Mapped[int] = mapped_column(primary_key=True)
    fridge_id: Mapped[int] = mapped_column(ForeignKey("fridges.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    min_c: Mapped[float] = mapped_column(Float)
    max_c: Mapped[float] = mapped_column(Float)
    minutes_out: Mapped[float] = mapped_column(Float, default=0.0)
    data_gap: Mapped[bool] = mapped_column(Boolean, default=False)
    alerted: Mapped[bool] = mapped_column(Boolean, default=False)
    assessed: Mapped[bool] = mapped_column(Boolean, default=False)

    fridge: Mapped[Fridge] = relationship()


class StabilityRule(Base):
    """A pharmacist-approved rule describing what temperature exposure a medicine tolerates.

    Only rules with status 'approved', a named approver and a published source are
    ever applied. Anything else makes the decision engine fall back to quarantine.
    """

    __tablename__ = "stability_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    match_gtin: Mapped[str | None] = mapped_column(String(14), index=True)
    match_name: Mapped[str | None] = mapped_column(String(200))  # case-insensitive substring
    # Discard if the item was ever colder than this (freezing destroys many biologics).
    min_allowed_c: Mapped[float] = mapped_column(Float, default=2.0)
    # Discard if the item was ever warmer than this.
    max_allowed_c: Mapped[float] = mapped_column(Float, default=8.0)
    # Discard if cumulative time outside 2-8 C exceeds this many minutes.
    max_minutes_out: Mapped[float] = mapped_column(Float, default=0.0)
    # Within limits: shorten expiry to this many days from the excursion (None = keep expiry).
    new_expiry_days: Mapped[int | None] = mapped_column(Integer)
    source_reference: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    approved_by_name: Mapped[str | None] = mapped_column(String(200))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    @property
    def usable(self) -> bool:
        return bool(self.status == "approved" and self.approved_by_name and self.source_reference)


class ExcursionDecision(Base):
    __tablename__ = "excursion_decisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    excursion_id: Mapped[int] = mapped_column(ForeignKey("excursions.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("medicine_items.id"))
    decision: Mapped[str] = mapped_column(String(20))
    instruction: Mapped[str] = mapped_column(Text)
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("stability_rules.id"))
    new_expiry: Mapped[date | None] = mapped_column(Date)
    acknowledged_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    item: Mapped[MedicineItem] = relationship()


class DeliveryBox(Base):
    """A reusable pharmacy delivery box with a temperature logger tag."""

    __tablename__ = "delivery_boxes"
    id: Mapped[int] = mapped_column(primary_key=True)
    pharmacy_id: Mapped[int] = mapped_column(ForeignKey("pharmacies.id"))
    code: Mapped[str] = mapped_column(String(100), unique=True)  # QR on the box
    tag_external_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Delivery(Base):
    __tablename__ = "deliveries"
    id: Mapped[int] = mapped_column(primary_key=True)
    box_id: Mapped[int] = mapped_column(ForeignKey("delivery_boxes.id"))
    home_id: Mapped[int] = mapped_column(ForeignKey("homes.id"))
    pharmacy_id: Mapped[int] = mapped_column(ForeignKey("pharmacies.id"))
    dispatched_at: Mapped[datetime] = mapped_column(DateTime)
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="in_transit")  # in_transit | accepted | refused
    result_reason: Mapped[str | None] = mapped_column(Text)
    checked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    contents_note: Mapped[str | None] = mapped_column(Text)

    box: Mapped[DeliveryBox] = relationship()


class ReplacementRequest(Base):
    __tablename__ = "replacement_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    home_id: Mapped[int] = mapped_column(ForeignKey("homes.id"))
    pharmacy_id: Mapped[int | None] = mapped_column(ForeignKey("pharmacies.id"))
    item_id: Mapped[int | None] = mapped_column(ForeignKey("medicine_items.id"))
    delivery_id: Mapped[int | None] = mapped_column(ForeignKey("deliveries.id"))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="requested")  # requested | sent | fulfilled
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    home_id: Mapped[int] = mapped_column(ForeignKey("homes.id"), index=True)
    type: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10), default="high")  # low | medium | high | critical
    title: Mapped[str] = mapped_column(String(300))
    message: Mapped[str] = mapped_column(Text)
    raised_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    seen_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    action_taken: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    context: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # De-duplication key so the same condition does not raise a second open alert.
    dedupe_key: Mapped[str | None] = mapped_column(String(200), index=True)


class Notification(Base):
    """Outbox of messages sent (or to be sent) to staff and pharmacies."""

    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int | None] = mapped_column(ForeignKey("alerts.id"))
    channel: Mapped[str] = mapped_column(String(20))  # app | sms | email
    recipient: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued | sent | failed
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)


class AuditEntry(Base):
    """Append-only, hash-chained audit log. Every action is timed and attributable."""

    __tablename__ = "audit_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    home_id: Mapped[int | None] = mapped_column(ForeignKey("homes.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str | None] = mapped_column(String(50))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class Interview(Base):
    """Customer discovery interview (Phase 1). Never stores resident information."""

    __tablename__ = "interviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    group: Mapped[str] = mapped_column(String(30))  # manager | carer | pharmacist | icb_pharmacist
    interviewee_label: Mapped[str] = mapped_column(String(200))
    organisation: Mapped[str | None] = mapped_column(String(200))
    held_on: Mapped[date] = mapped_column(Date)
    score_stock_mismatch: Mapped[int] = mapped_column(Integer, default=0)
    score_fridge_excursion: Mapped[int] = mapped_column(Integer, default=0)
    score_delivery_temperature: Mapped[int] = mapped_column(Integer, default=0)
    still_on_paper: Mapped[bool | None] = mapped_column(Boolean)
    money_or_time_spent: Mapped[str | None] = mapped_column(Text)
    workarounds: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    pilot_interest: Mapped[bool] = mapped_column(Boolean, default=False)
    written_up_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
