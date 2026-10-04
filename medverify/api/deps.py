"""Shared API dependencies: database session, authentication and access checks."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..models import Device, Home, User
from ..security import device_for_key, user_for_token

MANAGERS = ("manager", "group_admin", "admin")
SENIORS = ("senior",) + MANAGERS
STAFF = ("carer",) + SENIORS


def get_db(request: Request) -> Iterator[Session]:
    db = request.app.state.db.session()
    try:
        yield db
    finally:
        db.close()


def current_user(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in required")
    user = user_for_token(db, authorization.split(" ", 1)[1].strip())
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired, please sign in again")
    return user


def require(*roles: str):
    def check(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have permission to do this")
        return user

    return check


def can_access_home(user: User, home: Home) -> bool:
    if user.role == "admin":
        return True
    if user.role == "group_admin":
        return home.organisation_id == user.organisation_id
    if user.role == "pharmacist":
        # Pharmacist advisers see homes in their organisation (for quarantine questions).
        return user.organisation_id is not None and home.organisation_id == user.organisation_id
    return user.home_id == home.id


def home_or_404(db: Session, user: User, home_id: int) -> Home:
    home = db.get(Home, home_id)
    if home is None or not can_access_home(user, home):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Home not found")
    return home


def owned(db: Session, user: User, model, obj_id: int, home_attr: str = "home_id"):
    """Fetch a row belonging to a home the user can access, else 404."""
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    home_or_404(db, user, getattr(obj, home_attr))
    return obj


def current_device(x_device_id: str = Header(...), x_device_key: str = Header(...),
                   db: Session = Depends(get_db)) -> Device:
    device = device_for_key(db, x_device_id, x_device_key)
    if device is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown device or wrong key")
    return device
