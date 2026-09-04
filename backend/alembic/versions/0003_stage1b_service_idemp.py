"""Stage 1B: applications.service_id + application_idempotency_keys

Two changes, verified against `git diff 08b88b9..HEAD -- backend/app/models`
at the accepted Stage 1B baseline (commit 7f92965):

1. `applications.service_id` - nullable INTEGER, FK to admin_settings.id.
   Nullable at the DB level (not because a new submission may ever omit it -
   ApplicationCreate.service_id is required - but because a database
   upgraded from the legacy/Stage-1A baseline has historical rows with no
   service to point at and no deterministic way to infer one; see
   app/models/application.py's docstring). This is exactly the ALTER TABLE
   the old app/core/schema_compat.py used to perform at application startup
   - it is now a proper, reviewable migration instead, and startup no longer
   touches schema at all (see app/core/schema_check.py).

   The constraint name is left unnamed here (matching Postgres's own default
   "<table>_<column>_fkey" naming for an unnamed FK) so it comes out
   identical on both convergent paths: a fresh install creating this column
   via this ALTER TABLE, and a legacy database that already had the FK added
   by the historical schema_compat.py shim under the same default name
   before Stage 2 existed.

2. `application_idempotency_keys` - new table (see
   app/models/application_idempotency_key.py).

Revision ID: 0003_stage1b_service_idemp
Revises: 0002_stage1a_capability

(Revision id kept short - alembic_version.version_num is VARCHAR(32).)
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_stage1b_service_idemp"
down_revision: Union[str, None] = "0002_stage1a_capability"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("service_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "applications_service_id_fkey",
        "applications",
        "admin_settings",
        ["service_id"],
        ["id"],
    )

    op.create_table(
        "application_idempotency_keys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "application_id",
            sa.Integer(),
            sa.ForeignKey("applications.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("idempotency_key_hash"),
    )


def downgrade() -> None:
    op.drop_table("application_idempotency_keys")
    op.drop_constraint("applications_service_id_fkey", "applications", type_="foreignkey")
    op.drop_column("applications", "service_id")
