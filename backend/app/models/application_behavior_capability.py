"""SQLAlchemy model for one-time capability tokens gating behavior-metric submission.

Not the metrics themselves - see app/models/behavior_metric.py. A row here
proves nothing except "the holder of this token is the client that
POST /applications handed it to right after creating this exact
application" - it carries no data of its own, and unlike an Admin session
it authorizes exactly one thing: a single POST /behavior-metrics for the
one application_id it is bound to. It never authorizes reading, updating,
or deleting anything.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ApplicationBehaviorCapability(Base):
    """
    Example DDL (PostgreSQL):

    CREATE TABLE application_behavior_capabilities (
        id SERIAL PRIMARY KEY,
        application_id INTEGER NOT NULL UNIQUE
            REFERENCES applications(id) ON DELETE CASCADE,
        capability_hash VARCHAR(64) NOT NULL UNIQUE,
        used_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    application_id is UNIQUE: exactly one capability per application (see
    app.crud.application.create_application). capability_hash is the
    SHA-256 hex digest (see app.core.security.hash_capability_token) of the
    raw one-time token handed to the client - the raw value itself is never
    persisted anywhere. used_at is NULL until the one allowed submission
    consumes it (see app.crud.application_behavior_capability.
    try_consume_capability); a non-NULL used_at makes every subsequent
    attempt - including a replay of the very first request - fail.
    """

    __tablename__ = "application_behavior_capabilities"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), unique=True
    )
    capability_hash: Mapped[str] = mapped_column(String(64), unique=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
