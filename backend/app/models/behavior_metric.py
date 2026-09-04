"""SQLAlchemy model for user behavior metrics, one-to-one with an Application."""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Integer, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db_base import Base

if TYPE_CHECKING:
    from app.models.application import Application


class BehaviorMetric(Base):
    """
    Example DDL (PostgreSQL):

    CREATE TABLE behavior_metrics (
        id SERIAL PRIMARY KEY,
        application_id INTEGER NOT NULL UNIQUE
            REFERENCES applications(id) ON DELETE CASCADE,
        time_on_page INTEGER NOT NULL DEFAULT 0,
        clicked_buttons JSONB NOT NULL DEFAULT '[]'::jsonb,
        cursor_hover_data JSONB NOT NULL DEFAULT '{}'::jsonb,
        return_count INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """

    __tablename__ = "behavior_metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), unique=True
    )
    time_on_page: Mapped[int] = mapped_column(Integer, default=0)
    # JSONB: список/структура кликов и агрегированные данные по наведению
    # курсора — заранее неизвестной и изменяемой формы, поэтому
    # реляционная схема здесь неоправданна.
    clicked_buttons: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    cursor_hover_data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    return_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    application: Mapped["Application"] = relationship(back_populates="behavior_metric")
