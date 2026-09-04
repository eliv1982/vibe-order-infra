"""Stage 4: applications.priority_score

Adds a materialized, SQL-sortable copy of the existing (unchanged) business
priority score - app.services.application_scoring.score_application(...)
.score - so GET /applications/prioritized (app/routes/applications.py) can
ORDER BY + OFFSET/LIMIT this column directly in PostgreSQL instead of
loading the entire applications table into Python to score and sort it
there on every request (the Stage 4 fix for that scalability defect - see
app/models/application.py's priority_score docstring, and the
_sync_priority_score mapper event next to it, which keeps this column in
sync with score_application() going forward for every insert/update).

This migration only backfills *historical* rows so the new NOT NULL column
can be added safely - it does not change, invent, or reinterpret any scoring
semantics: every backfilled value is produced by calling the exact same
score_application() the application already used (and still uses) to
compute this same number for display. Historical rows therefore end up with
the identical priority_score a live request would have shown for them
before this migration - nothing about how a row scores changes.

Column added in one step as NOT NULL DEFAULT 0 (a constant default, so
PostgreSQL 11+ populates every existing row instantly via a catalog-only
"missing value", not a full table rewrite - see ADD COLUMN's documentation)
- the same server_default the column keeps permanently afterward (see
app/models/application.py's matching server_default="0", and its docstring
for why: a NOT NULL safety net for a row written by something other than
this application's own ORM, e.g. a raw-SQL test/maintenance script against
the runtime role). The 0 placeholder is then overwritten row by row with
each historical row's real computed score (this project's dataset is small
- see app/crud/application.py::get_applications_created_between's docstring
for the same assumption elsewhere). A plain single-column index (not a
mixed-direction composite one) keeps this migration - and the SQLAlchemy
metadata in app/models/application.py it must stay in lockstep with for
`alembic check` (tests/test_migrations_drift.py) - simple and unambiguous;
Postgres can still use it (scanned backwards) for `ORDER BY priority_score
DESC`, and the remaining tie-break (created_at, id) is cheap for this
project's data volumes to resolve with an ordinary sort over the already-
narrowed result.

Revision ID: 0004_stage4_priority_score
Revises: 0003_stage1b_service_idemp

(Revision id kept short - alembic_version.version_num is VARCHAR(32).)
"""

from types import SimpleNamespace
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.services.application_scoring import score_application

# revision identifiers, used by Alembic.
revision: str = "0004_stage4_priority_score"
down_revision: Union[str, None] = "0003_stage1b_service_idemp"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Exactly the ScorableApplication protocol's attributes (see
# app/services/application_scoring.py) - enough to call score_application()
# against a plain row, without needing the ORM model (migrations must keep
# working against whatever the schema looked like at the time they were
# written, not today's app/models/application.py).
_SCORE_COLUMNS = (
    "id",
    "first_name",
    "last_name",
    "contact_data",
    "business_niche",
    "company_size",
    "business_info",
    "task_scope",
    "requester_role",
    "business_size",
    "need_scope",
    "deadline",
    "task_type",
    "interested_product",
    "budget",
    "preferred_contact_method",
    "preferred_contact_time",
    "comment",
)


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column("priority_score", sa.Integer(), nullable=False, server_default="0"),
    )

    conn = op.get_bind()
    rows = conn.execute(
        sa.text(f"SELECT {', '.join(_SCORE_COLUMNS)} FROM applications")
    ).mappings().all()
    for row in rows:
        score = score_application(SimpleNamespace(**row)).score
        conn.execute(
            sa.text("UPDATE applications SET priority_score = :score WHERE id = :id"),
            {"score": score, "id": row["id"]},
        )

    op.create_index("ix_applications_priority_score", "applications", ["priority_score"])


def downgrade() -> None:
    op.drop_index("ix_applications_priority_score", table_name="applications")
    op.drop_column("applications", "priority_score")
