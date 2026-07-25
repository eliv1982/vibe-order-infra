"""Response schemas for the protected behavior-metrics analytics endpoints.

The aggregation logic itself lives in app.services.behavior_analytics; this
module only shapes the API-facing result. Deliberately excluded from every
model here: any Application field beyond its bare id (no name, contact_data,
business_info, need_scope, comment, ...), any admin/JWT data, and raw
JSON payloads — only aggregated, typed numbers/strings ever cross into
these response models.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Period = Literal["day", "week", "month"]


class ButtonAnalyticsItem(BaseModel):
    name: str
    count: int = Field(..., ge=0)
    share_percent: float = Field(..., ge=0, le=100)


class SectionAnalyticsItem(BaseModel):
    """Reflects the actual shape of cursor_hover_data: a named form section
    with an interaction count and a total/average hover duration. There are
    no coordinates in the underlying data, so this is deliberately never
    presented as a heatmap."""

    section: str
    total_duration_seconds: float = Field(..., ge=0)
    average_duration_seconds: float = Field(..., ge=0)
    interactions_count: int = Field(..., ge=0)
    share_percent: float = Field(..., ge=0, le=100)


class AnalyticsOverview(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    period: Period
    period_start: datetime
    period_end: datetime
    applications_count: int = Field(..., ge=0)
    metrics_count: int = Field(..., ge=0)
    applications_with_metrics: int = Field(..., ge=0)
    applications_without_metrics: int = Field(..., ge=0)
    average_time_on_page_seconds: float | None = Field(None, ge=0)
    median_time_on_page_seconds: float | None = Field(None, ge=0)
    average_return_count: float | None = Field(None, ge=0)
    total_return_count: int = Field(..., ge=0)
    total_button_clicks: int = Field(..., ge=0)
    unique_clicked_buttons: int = Field(..., ge=0)
    popular_buttons: list[ButtonAnalyticsItem] = Field(default_factory=list)
    section_activity: list[SectionAnalyticsItem] = Field(default_factory=list)


class ApplicationBehaviorAnalyticsRead(BaseModel):
    """Behavior-metric aggregates for one application — no applicant PII,
    no full Application, only the id plus aggregated behavior data."""

    application_id: int = Field(..., gt=0)
    has_metrics: bool
    time_on_page_seconds: float | None = Field(None, ge=0)
    return_count: int | None = Field(None, ge=0)
    clicked_buttons: list[ButtonAnalyticsItem] = Field(default_factory=list)
    section_activity: list[SectionAnalyticsItem] = Field(default_factory=list)
    total_button_clicks: int = Field(..., ge=0)
    recorded_at: datetime | None = None
