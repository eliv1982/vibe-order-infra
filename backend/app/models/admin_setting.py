"""SQLAlchemy model for admin-managed services used to build the client-facing form."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AdminSetting(Base):
    """
    Example DDL (PostgreSQL):

    CREATE TABLE admin_settings (
        id SERIAL PRIMARY KEY,
        service_name VARCHAR(255) NOT NULL,
        budget_min NUMERIC(12, 2) NOT NULL,
        budget_max NUMERIC(12, 2) NOT NULL,
        description TEXT,
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """

    __tablename__ = "admin_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    service_name: Mapped[str] = mapped_column(String(255))
    budget_min: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    budget_max: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
