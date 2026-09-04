"""Legacy production baseline (pre-Stage-1A)

This revision's upgrade() recreates the exact schema that was already
deployed to production before Stage 1A/1B existed - verified against
`git show e93238b:backend/app/models/` (the last commit before Stage 1A's
`application_behavior_capabilities` table and Stage 1B's `applications.
service_id`/`application_idempotency_keys` were introduced): admins,
admin_settings, applications (no service_id column) and behavior_metrics.

Two paths converge on this revision:

- Fresh install: `alembic upgrade head` starts here, from an empty
  database, then applies 0002/0003 on top - see tests/
  test_migrations_fresh_install.py.
- Existing production-like database: never runs this revision's upgrade()
  at all. app/db_admin/adopt_legacy.py verifies the live database already
  has exactly this shape, then stamps it directly at this revision (see
  alembic_version), so `alembic upgrade head` only has to apply 0002/0003 -
  see tests/test_migrations_legacy_upgrade.py. upgrade() is therefore never
  actually executed against a real legacy database; it exists so a *fresh*
  database can reach the same schema a legacy one already has.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_legacy_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admins",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(length=150), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("username"),
    )

    op.create_table(
        "admin_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("service_name", sa.String(length=255), nullable=False),
        sa.Column("budget_min", sa.Numeric(12, 2), nullable=False),
        sa.Column("budget_max", sa.Numeric(12, 2), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "applications",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("first_name", sa.String(length=100), nullable=False),
        sa.Column("last_name", sa.String(length=100), nullable=False),
        sa.Column("middle_name", sa.String(length=100), nullable=True),
        sa.Column("contact_data", sa.String(length=255), nullable=False),
        sa.Column("business_niche", sa.String(length=255), nullable=False),
        sa.Column("company_size", sa.String(length=50), nullable=False),
        sa.Column("business_info", sa.Text(), nullable=False),
        sa.Column("task_scope", sa.Text(), nullable=False),
        sa.Column("requester_role", sa.String(length=50), nullable=False),
        sa.Column("business_size", sa.String(length=50), nullable=False),
        sa.Column("need_scope", sa.Text(), nullable=False),
        sa.Column("deadline", sa.String(length=100), nullable=False),
        sa.Column("task_type", sa.String(length=100), nullable=False),
        sa.Column("interested_product", sa.String(length=255), nullable=False),
        sa.Column("budget", sa.Numeric(12, 2), nullable=False),
        sa.Column("preferred_contact_method", sa.String(length=50), nullable=False),
        sa.Column("preferred_contact_time", sa.String(length=100), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "behavior_metrics",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "application_id",
            sa.Integer(),
            sa.ForeignKey("applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("time_on_page", sa.Integer(), nullable=False),
        sa.Column("clicked_buttons", postgresql.JSONB(), nullable=False),
        sa.Column("cursor_hover_data", postgresql.JSONB(), nullable=False),
        sa.Column("return_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("application_id"),
    )


def downgrade() -> None:
    op.drop_table("behavior_metrics")
    op.drop_table("applications")
    op.drop_table("admin_settings")
    op.drop_table("admins")
