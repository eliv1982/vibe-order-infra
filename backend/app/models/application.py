"""SQLAlchemy model for client applications (клиентские заявки)."""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.behavior_metric import BehaviorMetric


class Application(Base):
    """
    Example DDL (PostgreSQL):

    CREATE TABLE applications (
        id SERIAL PRIMARY KEY,
        first_name VARCHAR(100) NOT NULL,
        last_name VARCHAR(100) NOT NULL,
        middle_name VARCHAR(100),
        contact_data VARCHAR(255) NOT NULL,
        business_niche VARCHAR(255) NOT NULL,
        company_size VARCHAR(50) NOT NULL,
        business_info TEXT NOT NULL,
        task_scope TEXT NOT NULL,
        requester_role VARCHAR(50) NOT NULL,
        business_size VARCHAR(50) NOT NULL,
        need_scope TEXT NOT NULL,
        deadline VARCHAR(100) NOT NULL,
        task_type VARCHAR(100) NOT NULL,
        service_id INTEGER NULL REFERENCES admin_settings(id),
        interested_product VARCHAR(255) NOT NULL,
        budget NUMERIC(12, 2) NOT NULL,
        preferred_contact_method VARCHAR(50) NOT NULL,
        preferred_contact_time VARCHAR(100) NOT NULL,
        comment TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    service_id/interested_product (see app/schemas/application.py): the
    client only ever chooses a service_id - a stable AdminSetting id, looked
    up and validated (exists, is_active, budget within [budget_min,
    budget_max]) server-side at creation (app/crud/application.py). The
    client never supplies interested_product directly; it is a denormalized
    snapshot of that service's service_name at submission time, so the
    applicant's stated intent survives even if the service is later renamed
    (or deactivated - service_id has no ON DELETE CASCADE, so a service with
    existing applications cannot be deleted at all; see
    app/crud/admin_setting.py).

    service_id is nullable at the DB/model level - not because a new
    submission is ever allowed to omit it (ApplicationCreate.service_id is
    required, see app/schemas/application.py), but because a database
    upgraded from the accepted Stage 1A baseline (see
    app/core/schema_compat.py) has historical rows with no service to point
    at and no deterministic way to infer one from their free-text
    interested_product alone. ApplicationRead.service_id is therefore also
    Optional, so those historical rows keep reading back correctly.
    """

    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    middle_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    contact_data: Mapped[str] = mapped_column(String(255))
    business_niche: Mapped[str] = mapped_column(String(255))
    company_size: Mapped[str] = mapped_column(String(50))
    business_info: Mapped[str] = mapped_column(Text)
    task_scope: Mapped[str] = mapped_column(Text)
    requester_role: Mapped[str] = mapped_column(String(50))
    business_size: Mapped[str] = mapped_column(String(50))
    need_scope: Mapped[str] = mapped_column(Text)
    deadline: Mapped[str] = mapped_column(String(100))
    task_type: Mapped[str] = mapped_column(String(100))
    service_id: Mapped[int | None] = mapped_column(ForeignKey("admin_settings.id"), nullable=True)
    interested_product: Mapped[str] = mapped_column(String(255))
    budget: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    preferred_contact_method: Mapped[str] = mapped_column(String(50))
    preferred_contact_time: Mapped[str] = mapped_column(String(100))
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    behavior_metric: Mapped["BehaviorMetric | None"] = relationship(
        back_populates="application",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
