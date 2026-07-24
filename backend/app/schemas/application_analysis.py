"""Response schemas for admin application-priority scoring.

The scoring rules themselves live in app.services.application_scoring;
this module only shapes the API-facing result (no ORM objects, no
password-related data, no raw exceptions ever cross into these models).
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.application import ApplicationRead


class ScoringReason(BaseModel):
    code: str
    points: int
    label: str


class ApplicationPriorityRead(BaseModel):
    application: ApplicationRead
    priority_score: int = Field(..., ge=0, le=100)
    priority_level: Literal["hot", "medium", "low"]
    priority_label: str
    reasons: list[ScoringReason]
    recommended_action: str
    recommended_team: str
    requires_personal_manager: bool


class PrioritizedApplicationList(BaseModel):
    items: list[ApplicationPriorityRead]
    total: int
    skip: int
    limit: int
