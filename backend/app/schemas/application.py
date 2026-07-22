"""Pydantic schemas for the Application entity: create / update / read."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Columns that are NOT NULL in the applications table (see
# app/models/application.py). PATCH must reject an explicit null for
# these — omitting the field is fine (exclude_unset handles that in crud),
# but {"first_name": null} would otherwise reach PostgreSQL as a NOT NULL
# violation instead of failing validation up front. middle_name/comment are
# nullable and intentionally excluded so they can still be cleared via null.
_REQUIRED_UPDATE_FIELDS = (
    "first_name",
    "last_name",
    "contact_data",
    "business_niche",
    "company_size",
    "business_info",
    "task_scope",
    "requester_role",
    "business_size",
    "need_scope",
    "deadline",
    "task_type",
    "interested_product",
    "budget",
    "preferred_contact_method",
    "preferred_contact_time",
)


class ApplicationBase(BaseModel):
    first_name: str = Field(..., max_length=100)
    last_name: str = Field(..., max_length=100)
    middle_name: str | None = Field(None, max_length=100)
    contact_data: str = Field(..., max_length=255)
    business_niche: str = Field(..., max_length=255)
    company_size: str = Field(..., max_length=50)
    business_info: str
    task_scope: str
    requester_role: str = Field(..., max_length=50)
    business_size: str = Field(..., max_length=50)
    need_scope: str
    deadline: str = Field(..., max_length=100)
    task_type: str = Field(..., max_length=100)
    interested_product: str = Field(..., max_length=255)
    budget: Decimal = Field(..., ge=0)
    preferred_contact_method: str = Field(..., max_length=50)
    preferred_contact_time: str = Field(..., max_length=100)
    comment: str | None = None


class ApplicationCreate(ApplicationBase):
    pass


class ApplicationUpdate(BaseModel):
    first_name: str | None = Field(None, max_length=100)
    last_name: str | None = Field(None, max_length=100)
    middle_name: str | None = Field(None, max_length=100)
    contact_data: str | None = Field(None, max_length=255)
    business_niche: str | None = Field(None, max_length=255)
    company_size: str | None = Field(None, max_length=50)
    business_info: str | None = None
    task_scope: str | None = None
    requester_role: str | None = Field(None, max_length=50)
    business_size: str | None = Field(None, max_length=50)
    need_scope: str | None = None
    deadline: str | None = Field(None, max_length=100)
    task_type: str | None = Field(None, max_length=100)
    interested_product: str | None = Field(None, max_length=255)
    budget: Decimal | None = Field(None, ge=0)
    preferred_contact_method: str | None = Field(None, max_length=50)
    preferred_contact_time: str | None = Field(None, max_length=100)
    comment: str | None = None

    @model_validator(mode="after")
    def _reject_explicit_null_for_required_fields(self) -> "ApplicationUpdate":
        set_fields = self.model_fields_set
        nulled = [
            name
            for name in _REQUIRED_UPDATE_FIELDS
            if name in set_fields and getattr(self, name) is None
        ]
        if nulled:
            raise ValueError(f"Fields cannot be explicitly set to null: {', '.join(nulled)}")
        return self


class ApplicationRead(ApplicationBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
