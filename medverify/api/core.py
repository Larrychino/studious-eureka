"""Sign-in, homes and their set-up: fridges, residents, trays, staff and devices."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..config import settings
from ..models import ROLES, Device, Fridge, Home, Resident, Tray, User
from ..register import RegisterError, get_or_create_resident
from ..security import hash_password, issue_token, new_device_key, revoke_token, sha256, verify_password
from ..temperature import open_excursion
from . import serialize as S
from .deps import MANAGERS, current_user, get_db, home_or_404, owned, require

router = APIRouter(prefix="/api")


class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == body.username.strip().lower()))
    if user is None or not user.active or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Wrong username or password")
    token = issue_token(db, user, settings.token_ttl_hours)
    audit.record(db, "user.signed_in", actor=user, home_id=user.home_id, entity_type="user", entity_id=user.id)
    db.commit()
    return {"token": token, "user": S.user(user)}


@router.post("/auth/logout")
def logout(authorization: str = Header(...), db: Session = Depends(get_db), user: User = Depends(current_user)):
    revoke_token(db, authorization.split(" ", 1)[1].strip())
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return S.user(user)


class ShiftIn(BaseModel):
    on_shift: bool


@router.post("/me/shift")
def set_shift(body: ShiftIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    user = db.merge(user)
    user.on_shift = body.on_shift
    audit.record(db, "user.shift_started" if body.on_shift else "user.shift_ended", actor=user,
                 home_id=user.home_id, entity_type="user", entity_id=user.id)
    db.commit()
    return S.user(user)


# ---- Homes ----------------------------------------------------------------

@router.get("/homes")
def list_homes(db: Session = Depends(get_db), user: User = Depends(current_user)):
    from .deps import can_access_home

    return [S.home(h) for h in db.scalars(select(Home).order_by(Home.name)) if can_access_home(user, h)]


@router.get("/homes/{home_id}")
def get_home(home_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return S.home(home_or_404(db, user, home_id))


class HomeSettingsIn(BaseModel):
    allow_resident_names: bool | None = None
    uses_digital_mar: bool | None = None
    emar_system: str | None = None
    reconcile_window_min: int | None = Field(default=None, ge=5, le=240)
    excursion_grace_min: int | None = Field(default=None, ge=0, le=120)
    sensor_offline_min: int | None = Field(default=None, ge=10, le=1440)
    door_open_alert_min: int | None = Field(default=None, ge=1, le=60)
    cqc_location_id: str | None = None


@router.patch("/homes/{home_id}")
def update_home(home_id: int, body: HomeSettingsIn, db: Session = Depends(get_db),
                user: User = Depends(require(*MANAGERS))):
    home = home_or_404(db, user, home_id)
    changes = body.model_dump(exclude_none=True)
    for k, v in changes.items():
        setattr(home, k, v)
    audit.record(db, "home.settings_changed", actor=user, home_id=home.id, entity_type="home", entity_id=home.id,
                 details=changes)
    db.commit()
    return S.home(home)


# ---- Fridges --------------------------------------------------------------

@router.get("/homes/{home_id}/fridges")
def list_fridges(home_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    home_or_404(db, user, home_id)
    return [S.fridge(f, open_excursion(db, f)) for f in db.scalars(select(Fridge).where(Fridge.home_id == home_id))]


class FridgeIn(BaseModel):
    name: str
    min_c: float = 2.0
    max_c: float = 8.0


@router.post("/homes/{home_id}/fridges")
def create_fridge(home_id: int, body: FridgeIn, db: Session = Depends(get_db),
                  user: User = Depends(require(*MANAGERS))):
    home_or_404(db, user, home_id)
    if body.min_c >= body.max_c:
        raise HTTPException(422, "Minimum must be below maximum")
    f = Fridge(home_id=home_id, name=body.name, min_c=body.min_c, max_c=body.max_c)
    db.add(f)
    db.flush()
    audit.record(db, "fridge.created", actor=user, home_id=home_id, entity_type="fridge", entity_id=f.id,
                 details=body.model_dump())
    db.commit()
    return S.fridge(f)


# ---- Residents (internal reference only unless the data agreement allows names) ----

@router.get("/homes/{home_id}/residents")
def list_residents(home_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    home_or_404(db, user, home_id)
    return [S.resident(r) for r in db.scalars(select(Resident).where(Resident.home_id == home_id).order_by(Resident.ref))]


class ResidentIn(BaseModel):
    ref: str = Field(min_length=1, max_length=50)
    display_name: str | None = None
    room: str | None = None


@router.post("/homes/{home_id}/residents")
def create_resident(home_id: int, body: ResidentIn, db: Session = Depends(get_db),
                    user: User = Depends(require("senior", *MANAGERS))):
    home = home_or_404(db, user, home_id)
    try:
        r = get_or_create_resident(db, home, body.ref.strip(), body.display_name)
    except RegisterError as exc:
        raise HTTPException(422, str(exc)) from exc
    r.room = body.room
    audit.record(db, "resident.created", actor=user, home_id=home_id, entity_type="resident", entity_id=r.id,
                 details={"ref": r.ref})
    db.commit()
    return S.resident(r)


# ---- Trays ------------------------------------------------------------------

@router.get("/homes/{home_id}/trays")
def list_trays(home_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    home_or_404(db, user, home_id)
    return [S.tray(t) for t in db.scalars(select(Tray).where(Tray.home_id == home_id).order_by(Tray.label))]


class TrayIn(BaseModel):
    label: str
    fridge_id: int
    resident_id: int | None = None


@router.post("/homes/{home_id}/trays")
def create_tray(home_id: int, body: TrayIn, db: Session = Depends(get_db),
                user: User = Depends(require("senior", *MANAGERS))):
    home_or_404(db, user, home_id)
    fridge = db.get(Fridge, body.fridge_id)
    if fridge is None or fridge.home_id != home_id:
        raise HTTPException(422, "Fridge not found in this home")
    if body.resident_id:
        r = db.get(Resident, body.resident_id)
        if r is None or r.home_id != home_id:
            raise HTTPException(422, "Resident not found in this home")
    t = Tray(home_id=home_id, fridge_id=body.fridge_id, resident_id=body.resident_id, label=body.label)
    db.add(t)
    db.flush()
    audit.record(db, "tray.created", actor=user, home_id=home_id, entity_type="tray", entity_id=t.id,
                 details=body.model_dump())
    db.commit()
    return S.tray(t)


class TrayAssignIn(BaseModel):
    resident_id: int | None


@router.post("/trays/{tray_id}/assign")
def assign_tray(tray_id: int, body: TrayAssignIn, db: Session = Depends(get_db),
                user: User = Depends(require("senior", *MANAGERS))):
    t = owned(db, user, Tray, tray_id)
    if body.resident_id:
        r = db.get(Resident, body.resident_id)
        if r is None or r.home_id != t.home_id:
            raise HTTPException(422, "Resident not found in this home")
    t.resident_id = body.resident_id
    audit.record(db, "tray.assigned", actor=user, home_id=t.home_id, entity_type="tray", entity_id=t.id,
                 details={"resident_id": body.resident_id})
    db.commit()
    return S.tray(t)


# ---- Staff ------------------------------------------------------------------

@router.get("/homes/{home_id}/users")
def list_users(home_id: int, db: Session = Depends(get_db), user: User = Depends(require(*MANAGERS))):
    home_or_404(db, user, home_id)
    return [S.user(u) for u in db.scalars(select(User).where(User.home_id == home_id).order_by(User.display_name))]


class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    display_name: str
    role: str
    password: str = Field(min_length=8)
    phone: str | None = None


@router.post("/homes/{home_id}/users")
def create_user(home_id: int, body: UserIn, db: Session = Depends(get_db), user: User = Depends(require(*MANAGERS))):
    home = home_or_404(db, user, home_id)
    if body.role not in ("carer", "senior", "manager"):
        raise HTTPException(422, "Home staff can be carer, senior or manager")
    username = body.username.strip().lower()
    if db.scalar(select(User).where(User.username == username)):
        raise HTTPException(409, "That username is taken")
    u = User(username=username, display_name=body.display_name, role=body.role, home_id=home.id,
             organisation_id=home.organisation_id, password_hash=hash_password(body.password), phone=body.phone)
    db.add(u)
    db.flush()
    audit.record(db, "user.created", actor=user, home_id=home.id, entity_type="user", entity_id=u.id,
                 details={"username": username, "role": body.role})
    db.commit()
    return S.user(u)


# ---- Devices ------------------------------------------------------------------

class DeviceIn(BaseModel):
    kind: str  # fridge_sensor | weight_tray
    external_id: str = Field(min_length=3, max_length=100)
    fridge_id: int | None = None
    tray_id: int | None = None


@router.post("/homes/{home_id}/devices")
def register_device(home_id: int, body: DeviceIn, db: Session = Depends(get_db),
                    user: User = Depends(require(*MANAGERS))):
    """Register a sensor or tray. The API key is shown once; store it on the device."""
    home_or_404(db, user, home_id)
    if db.scalar(select(Device).where(Device.external_id == body.external_id)):
        raise HTTPException(409, "A device with that ID is already registered")
    key = new_device_key()
    device = Device(kind=body.kind, home_id=home_id, external_id=body.external_id, api_key_hash=sha256(key))
    db.add(device)
    db.flush()
    if body.kind == "fridge_sensor":
        fridge = db.get(Fridge, body.fridge_id) if body.fridge_id else None
        if fridge is None or fridge.home_id != home_id:
            raise HTTPException(422, "Choose the fridge this sensor is in")
        fridge.sensor_device_id = device.id
    elif body.kind == "weight_tray":
        tray = db.get(Tray, body.tray_id) if body.tray_id else None
        if tray is None or tray.home_id != home_id:
            raise HTTPException(422, "Choose the tray this device is in")
        tray.device_id = device.id
    else:
        raise HTTPException(422, "Device kind must be fridge_sensor or weight_tray")
    audit.record(db, "device.registered", actor=user, home_id=home_id, entity_type="device", entity_id=device.id,
                 details={"kind": body.kind, "external_id": body.external_id})
    db.commit()
    return {"id": device.id, "external_id": device.external_id, "api_key": key}


@router.get("/roles")
def roles():
    return list(ROLES)
