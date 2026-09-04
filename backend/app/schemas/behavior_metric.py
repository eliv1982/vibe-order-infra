"""Pydantic schemas for the BehaviorMetric entity: create / update / read."""

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

# All updatable columns on behavior_metrics are NOT NULL (see
# app/models/behavior_metric.py) — PATCH must reject explicit null for
# every one of them; omitting a field is still fine.
_REQUIRED_UPDATE_FIELDS = ("time_on_page", "clicked_buttons", "cursor_hover_data", "return_count")

# `time_on_page`/`return_count` map to plain Postgres INTEGER columns
# (INT4 range: 0..~2.1 billion) — Stage 1B correction: these were
# previously bounded only by `ge=0`, so an absurdly large value (well
# within Python's unbounded int, but outside INT4) would pass schema
# validation and only fail at INSERT time as an unhandled SQLAlchemy
# DataError/500. The caps below are additionally much tighter than INT4's
# ceiling: `time_on_page` is elapsed seconds since page load (see
# frontend/src/metrics/behaviorMetrics.ts) and `return_count` a
# localStorage visit counter — neither can legitimately approach INT4's
# limit, so a sane application-level bound both prevents the DataError and
# rejects clearly-abusive values.
_TIME_ON_PAGE_MAX = 7 * 24 * 60 * 60  # 7 days, in seconds
_RETURN_COUNT_MAX = 100_000

# Bounds for the structured click/hover collections below. The real page
# tracks a small, fixed set of buttons/sections (see
# frontend/src/metrics/behaviorMetrics.ts) — these caps are generous, not
# tight, while still ruling out an arbitrarily large/deep payload.
_MAX_TRACKED_ITEMS = 200
_NAME_MAX_LENGTH = 100
_COUNT_MAX = 1_000_000
_MS_MAX = 30 * 24 * 60 * 60 * 1000  # 30 days, in milliseconds

_TrackedName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=_NAME_MAX_LENGTH)]


class ClickedButtonEntry(BaseModel):
    """One `{"button": <name>, "count": <clicks>}` entry - the shape
    frontend/src/metrics/behaviorMetrics.ts::summarizeClicks() always
    produces. extra="forbid" rejects an unexpected extra field outright
    (e.g. accidentally-collected free text) instead of silently dropping it
    - see Stage 1B requirement "do not accidentally collect... additional
    privacy-sensitive information"."""

    model_config = ConfigDict(extra="forbid")

    button: _TrackedName
    count: int = Field(..., ge=0, le=_COUNT_MAX)


class HoverEntry(BaseModel):
    """One section's aggregated hover stats - the shape
    frontend/src/metrics/behaviorMetrics.ts::summarizeHovers() always
    produces for one section key. No coordinates, no element identity - only
    a count and a duration in milliseconds."""

    model_config = ConfigDict(extra="forbid")

    hovers: int = Field(..., ge=0, le=_COUNT_MAX)
    ms: int = Field(..., ge=0, le=_MS_MAX)


class BehaviorMetricCreate(BaseModel):
    application_id: int = Field(..., gt=0)
    time_on_page: int = Field(0, ge=0, le=_TIME_ON_PAGE_MAX)
    # Stage 1B correction: previously `list[Any]`/`dict[str, Any]` -
    # unconstrained arbitrary nested JSON. Structured models below bound
    # both the shape of each entry and the total number of entries, so a
    # request can no longer submit an unbounded number of items or
    # arbitrary nested object contents.
    clicked_buttons: list[ClickedButtonEntry] = Field(default_factory=list, max_length=_MAX_TRACKED_ITEMS)
    cursor_hover_data: dict[_TrackedName, HoverEntry] = Field(
        default_factory=dict, max_length=_MAX_TRACKED_ITEMS
    )
    return_count: int = Field(0, ge=0, le=_RETURN_COUNT_MAX)

    # The one-time token returned as behavior_metrics_capability from
    # POST /applications (see app/schemas/application.py::ApplicationCreateRead).
    # Write-only - deliberately absent from BehaviorMetricRead, so it is
    # never echoed back in any response. See
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
    time_on_page: int | None = Field(None, ge=0, le=_TIME_ON_PAGE_MAX)
    clicked_buttons: list[ClickedButtonEntry] | None = Field(None, max_length=_MAX_TRACKED_ITEMS)
    cursor_hover_data: dict[_TrackedName, HoverEntry] | None = Field(None, max_length=_MAX_TRACKED_ITEMS)
    return_count: int | None = Field(None, ge=0, le=_RETURN_COUNT_MAX)

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


class BehaviorMetricRead(BaseModel):
    """Deliberately loosely-typed for clicked_buttons/cursor_hover_data
    (list[Any]/dict[str, Any]), unlike Create/Update above - a row written
    before this stage's structural validation existed (or inserted directly
    by a maintenance script/test, bypassing the API) may not match the
    now-strict shape, and a GET must still be able to render it rather than
    500 at response-validation time. app/services/behavior_analytics.py
    already treats these fields as `Any` for the same reason."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    application_id: int
    time_on_page: int
    clicked_buttons: list[Any]
    cursor_hover_data: dict[str, Any]
    return_count: int
    created_at: datetime
    updated_at: datetime
