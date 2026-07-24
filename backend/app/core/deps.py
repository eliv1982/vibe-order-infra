"""FastAPI dependencies for admin authentication."""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import TokenError, decode_access_token
from app.crud import admin as admin_crud
from app.models.admin import Admin

# auto_error=False so a *missing* Authorization header reaches this function
# too (FastAPI's default auto_error=True would raise its own 403 instead),
# letting every failure mode - missing, malformed, forged, expired, wrong
# type, unknown/inactive admin - converge on the same 401 response below.
_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> Admin:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None:
        raise unauthorized

    try:
        payload = decode_access_token(credentials.credentials)
    except TokenError as exc:
        raise unauthorized from exc

    try:
        admin_id = int(payload.get("sub", ""))
    except (TypeError, ValueError) as exc:
        raise unauthorized from exc

    admin = admin_crud.get_admin(db, admin_id)
    if admin is None or not admin.is_active:
        raise unauthorized

    return admin
