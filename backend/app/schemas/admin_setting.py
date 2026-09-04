"""Pydantic schemas for the AdminSetting entity: create / update / read."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

# NOT NULL columns on admin_settings (see app/models/admin_setting.py);
# "description" is nullable and intentionally excluded so it can still be
# cleared via explicit null.
_REQUIRED_UPDATE_FIELDS = ("service_name", "budget_min", "budget_max", "is_active")


def _required_text(max_length: int) -> type:
    """A required, whitespace-trimmed, non-blank string bounded to
    max_length. Mirrors app/schemas/application.py::_required_text (kept
    duplicated rather than shared, matching this file's existing pattern of
    a locally-defined helper - see _BUDGET_FIELD_KWARGS below).

    Stage 1B correction: service_name used to be enforced only by
    `max_length` - "" or "   " passed schema validation and was stored
    verbatim, then denormalized as-is into every new Application's
    interested_product (see app/crud/application.py). strip_whitespace=True
    also normalizes the *stored* value (leading/trailing whitespace trimmed
    before it reaches the database).
    """
    return Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=max_length)]


# NUMERIC(12, 2) on admin_settings.budget_min/budget_max (see
# app/models/admin_setting.py): max_digits=12/decimal_places=2 reject a
# value that would overflow or lose precision in that column - e.g.
# 12 digits before the point, or a 3rd decimal place - as a 422 up front
# instead of letting it reach PostgreSQL as an unhandled DataError.
_BUDGET_FIELD_KWARGS = {"ge": 0, "max_digits": 12, "decimal_places": 2}


class AdminSettingBase(BaseModel):
    service_name: _required_text(255)
    budget_min: Decimal = Field(..., **_BUDGET_FIELD_KWARGS)
    budget_max: Decimal = Field(..., **_BUDGET_FIELD_KWARGS)
    description: str | None = None
    is_active: bool = True


class AdminSettingCreate(AdminSettingBase):
    @model_validator(mode="after")
    def _validate_budget_range(self) -> "AdminSettingCreate":
        if self.budget_min > self.budget_max:
            raise ValueError("budget_min must be less than or equal to budget_max")
        return self


class AdminSettingUpdate(BaseModel):
    service_name: _required_text(255) | None = None
    budget_min: Decimal | None = Field(None, **_BUDGET_FIELD_KWARGS)
    budget_max: Decimal | None = Field(None, **_BUDGET_FIELD_KWARGS)
    description: str | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def _reject_explicit_null_for_required_fields(self) -> "AdminSettingUpdate":
        set_fields = self.model_fields_set
        nulled = [
            name
            for name in _REQUIRED_UPDATE_FIELDS
            if name in set_fields and getattr(self, name) is None
        ]
        if nulled:
            raise ValueError(f"Fields cannot be explicitly set to null: {', '.join(nulled)}")
        return self

    @model_validator(mode="after")
    def _validate_budget_range_if_both_present(self) -> "AdminSettingUpdate":
        # Fast-path check for the common case where both bounds are sent
        # together. When only one bound is patched, the authoritative check
        # runs in crud.update_admin_setting against the merged persisted
        # state — this schema has no access to the existing DB row.
        if (
            self.budget_min is not None
            and self.budget_max is not None
            and self.budget_min > self.budget_max
        ):
            raise ValueError("budget_min must be less than or equal to budget_max")
        return self


class AdminSettingRead(BaseModel):
    """Deliberately does NOT inherit AdminSettingBase's strict required_text
    service_name: a row written before this validation existed (e.g. one
    created with a blank/whitespace-only name before this Stage 1B
    correction) may not satisfy it, and a GET/listing endpoint must still be
    able to render such a row rather than fail response validation and 500.
    Writes are where the constraint matters and is enforced
    (AdminSettingCreate/AdminSettingUpdate above); reads simply reflect
    whatever is actually stored. Mirrors the same reasoning as
    app/schemas/application.py::ApplicationRead.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    service_name: str
    budget_min: Decimal
    budget_max: Decimal
    description: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
