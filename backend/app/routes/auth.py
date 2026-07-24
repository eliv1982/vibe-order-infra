"""HTTP routes for admin authentication: check, register, login, me."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_admin
from app.core.exceptions import ConflictError
from app.core.security import DUMMY_PASSWORD_HASH, create_access_token, verify_password
from app.crud import admin as admin_crud
from app.models.admin import Admin
from app.schemas.auth import (
    AdminLogin,
    AdminRead,
    AdminRegister,
    AuthCheckResponse,
    TokenResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/check", response_model=AuthCheckResponse)
def check_auth_status(db: Session = Depends(get_db)) -> AuthCheckResponse:
    admin_exists = admin_crud.count_admins(db) > 0
    return AuthCheckResponse(admin_exists=admin_exists, registration_allowed=not admin_exists)


@router.post("/register", response_model=AdminRead, status_code=status.HTTP_201_CREATED)
def register_first_admin(payload: AdminRegister, db: Session = Depends(get_db)) -> AdminRead:
    try:
        return admin_crud.register_first_admin(db, payload.username, payload.password)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/login", response_model=TokenResponse)
def login(payload: AdminLogin, db: Session = Depends(get_db)) -> TokenResponse:
    invalid_credentials = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect username or password",
        headers={"WWW-Authenticate": "Bearer"},
    )

    admin = admin_crud.get_admin_by_username(db, payload.username)
    # Always verify against *something* - the real hash if the admin exists,
    # a precomputed dummy hash otherwise - so an unknown username doesn't
    # short-circuit before hashing runs, keeping response timing the same
    # regardless of whether the username exists.
    password_hash = admin.password_hash if admin is not None else DUMMY_PASSWORD_HASH
    password_ok = verify_password(payload.password, password_hash)

    if admin is None or not admin.is_active or not password_ok:
        raise invalid_credentials

    access_token, expires_in = create_access_token(subject=str(admin.id))
    return TokenResponse(access_token=access_token, expires_in=expires_in)


@router.get("/me", response_model=AdminRead)
def read_current_admin(current_admin: Admin = Depends(get_current_admin)) -> AdminRead:
    return current_admin
