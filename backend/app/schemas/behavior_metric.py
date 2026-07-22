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
    pass


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
