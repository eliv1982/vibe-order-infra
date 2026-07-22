"""Pydantic schemas for the AdminSetting entity: create / update / read."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# NOT NULL columns on admin_settings (see app/models/admin_setting.py);
# "description" is nullable and intentionally excluded so it can still be
# cleared via explicit null.
_REQUIRED_UPDATE_FIELDS = ("service_name", "budget_min", "budget_max", "is_active")


class AdminSettingBase(BaseModel):
    service_name: str = Field(..., max_length=255)
    budget_min: Decimal = Field(..., ge=0)
    budget_max: Decimal = Field(..., ge=0)
    description: str | None = None
    is_active: bool = True


class AdminSettingCreate(AdminSettingBase):
    @model_validator(mode="after")
    def _validate_budget_range(self) -> "AdminSettingCreate":
        if self.budget_min > self.budget_max:
            raise ValueError("budget_min must be less than or equal to budget_max")
        return self


class AdminSettingUpdate(BaseModel):
    service_name: str | None = Field(None, max_length=255)
    budget_min: Decimal | None = Field(None, ge=0)
    budget_max: Decimal | None = Field(None, ge=0)
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


class AdminSettingRead(AdminSettingBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
