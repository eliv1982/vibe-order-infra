"""Pydantic schemas for the Application entity: create / update / read."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.schemas.application_options import (
    BusinessNiche,
    BusinessSize,
    CompanySize,
    Deadline,
    PreferredContactMethod,
    PreferredContactTime,
    RequesterRole,
    TaskScope,
    TaskType,
)

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


def _required_text(max_length: int) -> type:
    """A required, whitespace-trimmed, non-blank string bounded to
    max_length.

    Stage 1B correction: empty/whitespace-only values and unbounded length
    used to be enforced only by the frontend's `required`/`maxlength`
    attributes (or not at all, for business_info/need_scope) - a direct
    HTTP client could send "" or "   " or a multi-megabyte string. strip_
    whitespace=True also normalizes the *stored* value (leading/trailing
    whitespace is trimmed before it ever reaches the database).
    """
    return Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=max_length)]


# NUMERIC(12, 2) on applications.budget (see app/models/application.py) -
# max_digits=12/decimal_places=2 reject a value that would overflow or lose
# precision in that column as a 422 up front, instead of an unhandled
# SQLAlchemy DataError/500 (mirrors the same bound on admin_settings.
# budget_min/budget_max - see app/schemas/admin_setting.py). Whether the
# value falls within the *selected service's* [budget_min, budget_max] is a
# separate, service-specific check performed in app/crud/application.py -
# this only bounds it to what the column can physically store.
_BUDGET_FIELD_KWARGS = {"ge": 0, "max_digits": 12, "decimal_places": 2}


class ApplicationBase(BaseModel):
    first_name: _required_text(100)
    last_name: _required_text(100)
    middle_name: str | None = Field(None, max_length=100)
    contact_data: _required_text(255)
    # business_niche/company_size/business_size/requester_role/task_scope/
    # task_type/deadline/preferred_contact_method/preferred_contact_time are
    # all UI select/radio fields with a finite choice set (see
    # frontend/src/options.ts) - Stage 1B correction: the backend previously
    # stored these as unconstrained VARCHAR/TEXT, so a direct HTTP client
    # could send any string. Literal types (app/schemas/application_options.py)
    # make the backend authoritative over the same closed option sets.
    business_niche: BusinessNiche
    company_size: CompanySize
    business_info: _required_text(4000)
    task_scope: TaskScope
    requester_role: RequesterRole
    business_size: BusinessSize
    need_scope: _required_text(4000)
    deadline: Deadline
    task_type: TaskType
    budget: Decimal = Field(..., **_BUDGET_FIELD_KWARGS)
    preferred_contact_method: PreferredContactMethod
    preferred_contact_time: PreferredContactTime
    comment: str | None = Field(None, max_length=2000)

    @field_validator("middle_name", "comment", mode="before")
    @classmethod
    def _blank_optional_to_none(cls, value: object) -> object:
        """A blank-after-strip optional string normalizes to None rather
        than being rejected - "" and "omitted" mean the same thing for an
        optional field (the frontend already does this itself; this is the
        server-side equivalent for a direct HTTP client). Non-strings pass
        through unchanged so the ordinary type-validation error still fires."""
        if isinstance(value, str):
            stripped = value.strip()
            return stripped if stripped else None
        return value


class ApplicationCreate(ApplicationBase):
    # A stable server-side service identifier (AdminSetting.id) - Stage 1B
    # correction: the client used to send interested_product directly (any
    # string, including a service name that was never configured, or one
    # that was later deactivated). service_id is looked up and validated
    # (exists, is_active, budget within [budget_min, budget_max]) server-
    # side and atomically with the insert (see app/crud/application.py);
    # interested_product (see ApplicationRead below) is derived from that
    # lookup, never accepted from the client.
    service_id: int = Field(..., gt=0)


class ApplicationUpdate(BaseModel):
    first_name: _required_text(100) | None = None
    last_name: _required_text(100) | None = None
    middle_name: str | None = Field(None, max_length=100)
    contact_data: _required_text(255) | None = None
    business_niche: BusinessNiche | None = None
    company_size: CompanySize | None = None
    business_info: _required_text(4000) | None = None
    task_scope: TaskScope | None = None
    requester_role: RequesterRole | None = None
    business_size: BusinessSize | None = None
    need_scope: _required_text(4000) | None = None
    deadline: Deadline | None = None
    task_type: TaskType | None = None
    # Admin-only, free-text on purpose: PATCH has no service_id field (an
    # admin correcting a record edits the stored display name directly, not
    # the service association - reassigning service_id is out of scope).
    # Stage 1B correction: required/bounded like every other required-text
    # field (see _required_text above) - "" or "   " used to pass this
    # field's old bare `max_length` validation and be stored verbatim.
    interested_product: _required_text(255) | None = None
    budget: Decimal | None = Field(None, **_BUDGET_FIELD_KWARGS)
    preferred_contact_method: PreferredContactMethod | None = None
    preferred_contact_time: PreferredContactTime | None = None
    comment: str | None = Field(None, max_length=2000)

    @field_validator("middle_name", "comment", mode="before")
    @classmethod
    def _blank_optional_to_none(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped if stripped else None
        return value

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


class ApplicationRead(BaseModel):
    """Deliberately does NOT inherit ApplicationBase's strict Literal/bounded-
    text types: a row written before Stage 1B's validation existed (or
    inserted directly by a maintenance script/test, bypassing the API) may
    hold a value outside the current closed option sets, and a GET/listing
    endpoint must still be able to render it rather than fail response
    validation and 500. Writes are where these constraints matter and are
    enforced (ApplicationCreate/ApplicationUpdate above); reads simply
    reflect whatever is actually stored. Mirrors the same reasoning as
    app/schemas/behavior_metric.py::BehaviorMetricRead.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    first_name: str
    last_name: str
    middle_name: str | None
    contact_data: str
    business_niche: str
    company_size: str
    business_info: str
    task_scope: str
    requester_role: str
    business_size: str
    need_scope: str
    deadline: str
    task_type: str
    # Nullable: a historical row from a database upgraded from the accepted
    # Stage 1A baseline has no service to point at (see
    # app/core/schema_compat.py and app/models/application.py). Every row
    # created through Stage 1B's POST /applications always has a real,
    # FK-backed value here - see ApplicationCreate.service_id below.
    service_id: int | None
    interested_product: str
    budget: Decimal
    preferred_contact_method: str
    preferred_contact_time: str
    comment: str | None
    created_at: datetime
    updated_at: datetime


class ApplicationCreateRead(ApplicationRead):
    """Response for POST /applications only. Adds the behavior_metrics_capability
    token the client needs to submit behavior metrics for this application (see
    app/crud/application_behavior_capability.py and
    app/schemas/behavior_metric.py::BehaviorMetricCreate.capability). Never
    used for GET/PATCH - only ApplicationRead crosses those.

    Nullable (Stage 1B): a real, usable, one-time token is issued exactly
    once - on the winning, first-ever creation of the Application, whether
    that happened with or without an Idempotency-Key. Every idempotent
    replay (see Idempotency-Key handling in app/routes/applications.py) gets
    null here instead, regardless of whether the original capability is
    still unconsumed or was already used: the Idempotency-Key is
    caller-chosen and the backend cannot verify its entropy, so a successful
    replay must never be treated as authorization to mint or recover a
    capability (see app/crud/application.py::create_application_idempotent's
    module-level design note for the full rationale). The original raw token
    is never stored (only its digest - see
    app/crud/application_behavior_capability.py) so it genuinely cannot be
    recovered if the first response is lost - that loss is an accepted,
    intentional trade-off for Stage 1B, not an oversight.
    """

    behavior_metrics_capability: str | None
