"""Stage 1A: application_behavior_capabilities

Adds the one-time behavior-metrics submission capability table (see
app/models/application_behavior_capability.py). Verified against `git diff
e93238b..08b88b9 -- backend/app/models` - this is the *only* schema change
Stage 1A introduced; `applications`/`admin_settings`/`admins`/
`behavior_metrics` are untouched by this revision.

Revision ID: 0002_stage1a_capability
Revises: 0001_legacy_baseline

(Revision id kept short - alembic_version.version_num is VARCHAR(32) - see
tests/test_migrations_fresh_install.py, which exercises the real
alembic_version column, not just Python-side string comparisons.)
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_stage1a_capability"
down_revision: Union[str, None] = "0001_legacy_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "application_behavior_capabilities",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "application_id",
            sa.Integer(),
            sa.ForeignKey("applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("capability_hash", sa.String(length=64), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("application_id"),
        sa.UniqueConstraint("capability_hash"),
    )


def downgrade() -> None:
    op.drop_table("application_behavior_capabilities")
