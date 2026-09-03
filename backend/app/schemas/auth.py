"""Pydantic schemas for the admin authentication routes (routes/auth.py)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.security import normalize_username


class AdminRegister(BaseModel):
    """Validation for creating the first Admin.

    Not wired to any HTTP route - the public POST /auth/register endpoint
    was removed (see app/routes/auth.py and app/cli.py). This schema is now
    used only by the operator CLI bootstrap command, so the same
    username/password validation still applies to the one remaining way of
    creating an admin.
    """

    username: str = Field(..., min_length=3, max_length=150)
    password: str = Field(..., min_length=8, max_length=256)

    @field_validator("username", mode="before")
    @classmethod
    def _normalize_username(cls, value: object) -> object:
        # mode="before" so normalization (trim + case-fold) runs *before*
        # min_length/max_length are checked - otherwise e.g. "  ab  " could
        # pass the raw-length check and only turn out too short (or
        # whitespace-only input pass and turn out empty) after normalization
        # runs in a later, "after"-mode step. Non-string input is passed
        # through untouched so pydantic's own type validation reports it,
        # rather than duplicating that check here.
        if isinstance(value, str):
            return normalize_username(value)
        return value


class AdminLogin(BaseModel):
    # Deliberately not re-validating password strength here: a login attempt
    # must be checked against whatever the stored hash actually is, not
    # against today's registration policy. Only rejects empty input.
    username: str = Field(..., min_length=1, max_length=150)
    password: str = Field(..., min_length=1, max_length=256)

    @field_validator("username", mode="before")
    @classmethod
    def _normalize_username(cls, value: object) -> object:
        # mode="before" so normalization (trim + case-fold) runs *before*
        # min_length/max_length are checked - otherwise e.g. "  ab  " could
        # pass the raw-length check and only turn out too short (or
        # whitespace-only input pass and turn out empty) after normalization
        # runs in a later, "after"-mode step. Non-string input is passed
        # through untouched so pydantic's own type validation reports it,
        # rather than duplicating that check here.
        if isinstance(value, str):
            return normalize_username(value)
        return value


class AdminRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class AuthCheckResponse(BaseModel):
    """GET /auth/check's only public signal. Deliberately does not (and must
    never again) expose anything like "registration_allowed": whether an
    admin can be self-registered over public HTTP is no longer a concept
    this API has at all - the only way to create the first admin is the
    operator CLI (see app/cli.py)."""

    admin_exists: bool
