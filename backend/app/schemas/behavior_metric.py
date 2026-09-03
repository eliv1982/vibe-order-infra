"""Pydantic schemas for the BehaviorMetric entity: create / update / read."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

# All updatable columns on behavior_metrics are NOT NULL (see
# app/models/behavior_metric.py) — PATCH must reject explicit null for
# every one of them; omitting a field is still fine.
_REQUIRED_UPDATE_FIELDS = ("time_on_page", "clicked_buttons", "cursor_hover_data", "return_count")


class BehaviorMetricBase(BaseModel):
    application_id: int = Field(..., gt=0)
    time_on_page: int = Field(0, ge=0)
    clicked_buttons: list[Any] = Field(default_factory=list)
    cursor_hover_data: dict[str, Any] = Field(default_factory=dict)
    return_count: int = Field(0, ge=0)


class BehaviorMetricCreate(BehaviorMetricBase):
    # The one-time token returned as behavior_metrics_capability from
    # POST /applications (see app/schemas/application.py::ApplicationCreateRead).
    # Write-only - deliberately absent from BehaviorMetricBase/
    # BehaviorMetricRead, so it is never echoed back in any response. See
    # app/crud/application_behavior_capability.py for how it's verified.
    #
    # Optional/nullable (not `Field(...)`) *on purpose*, even though a real
    # submission always needs one: this lets the route
    # (routes/behavior_metrics.py) fold a missing/omitted/empty capability
    # into the exact same "no valid capability" outcome as a wrong one,
    # instead of a distinct 422 that would otherwise be reachable without
    # ever touching application_id - part of the single neutral
    # invalid-capability contract that endpoint enforces. A non-string
    # value (int/list/object) still fails ordinary schema validation (422),
    # since that rejection depends only on the field's shape, never on
    # whether the referenced application/capability exists.
    capability: str | None = Field(None, max_length=256)


class BehaviorMetricUpdate(BaseModel):
    time_on_page: int | None = Field(None, ge=0)
    clicked_buttons: list[Any] | None = None
    cursor_hover_data: dict[str, Any] | None = None
    return_count: int | None = Field(None, ge=0)

    @model_validator(mode="after")
    def _reject_explicit_null_for_required_fields(self) -> "BehaviorMetricUpdate":
        set_fields = self.model_fields_set
        nulled = [
            name
            for name in _REQUIRED_UPDATE_FIELDS
            if name in set_fields and getattr(self, name) is None
        ]
        if nulled:
            raise ValueError(f"Fields cannot be explicitly set to null: {', '.join(nulled)}")
        return self


class BehaviorMetricRead(BehaviorMetricBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
