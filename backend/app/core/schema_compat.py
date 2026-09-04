"""Narrow, idempotent pre-Alembic compatibility upgrade (Stage 1B correction).

Stage 2 will introduce real Alembic migrations. Until then, app.main's
lifespan only runs `Base.metadata.create_all(bind=engine)`, which CREATEs
tables that don't exist yet but never ALTERs a table that already does. A
database provisioned under the accepted Stage 1A baseline already has an
`applications` table - one with no `service_id` column, since that column
is new in Stage 1B (see app/models/application.py). Starting Stage 1B's app
against such a database would complete startup successfully (create_all has
nothing to do - the table exists) and then 500 on every query that touches
`Application.service_id`.

This module explicitly, idempotently adds exactly that one column/FK pair
before create_all runs (see app/main.py's lifespan), and nothing else - it
is deliberately not a general migration framework. Historical rows get a
NULL service_id (there is no authoritative, deterministic way to backfill
which service an old free-text `interested_product` corresponds to - see
app/models/application.py's docstring), which is why the column is nullable
both here and on the ORM model; ApplicationCreate/the route layer are what
actually require a valid service_id for every new public submission.
"""

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, ProgrammingError

_APPLICATIONS_TABLE = "applications"
_ADMIN_SETTINGS_TABLE = "admin_settings"
_SERVICE_ID_COLUMN = "service_id"


def _has_service_id_column(engine: Engine) -> bool:
    inspector = inspect(engine)
    if not inspector.has_table(_APPLICATIONS_TABLE):
        return True  # nothing to upgrade - create_all() will create it fresh, column included
    columns = {col["name"] for col in inspector.get_columns(_APPLICATIONS_TABLE)}
    return _SERVICE_ID_COLUMN in columns


def upgrade_applications_service_id(engine: Engine) -> None:
    """Add `applications.service_id` (nullable, FK to admin_settings.id) if
    an existing `applications` table doesn't already have it.

    Safe to call on every startup, against any database state:

    - Brand-new database (no `applications` table yet): no-op - create_all()
      creates the table with the current model definition, service_id
      included, in one step.
    - Already-upgraded database (Stage 1B ran before, or a fresh one that
      went through create_all()): the column is already present - no-op.
    - Genuine Stage 1A database (`applications` exists, no `service_id`):
      ALTER TABLE adds the column and its FK constraint as two explicit,
      separate statements in one transaction (PostgreSQL requires the column
      to exist before a constraint referencing it can be added).

    Inspects the schema itself rather than tracking a version number
    anywhere - there is exactly one upgrade this module ever needs to make,
    so "does the column exist" is a complete, always-accurate answer to
    "has this already run".
    """
    if _has_service_id_column(engine):
        return

    if not inspect(engine).has_table(_ADMIN_SETTINGS_TABLE):
        # Every Stage 1A deployment shipped admin_settings alongside
        # applications - if that invariant somehow doesn't hold, there is no
        # safe target for the FK constraint below; leave the table alone
        # rather than add an unconstrained column.
        return

    try:
        with engine.begin() as conn:
            conn.execute(
                text(f"ALTER TABLE {_APPLICATIONS_TABLE} ADD COLUMN {_SERVICE_ID_COLUMN} INTEGER NULL")
            )
            conn.execute(
                text(
                    f"ALTER TABLE {_APPLICATIONS_TABLE} "
                    f"ADD CONSTRAINT applications_service_id_fkey "
                    f"FOREIGN KEY ({_SERVICE_ID_COLUMN}) REFERENCES {_ADMIN_SETTINGS_TABLE} (id)"
                )
            )
    except (ProgrammingError, IntegrityError):
        # Another process concurrently applied the same upgrade between our
        # check above and this transaction (e.g. two backend replicas
        # starting at once) - safe to ignore only if the column really is
        # there now; otherwise this was a genuine failure and must surface.
        if not _has_service_id_column(engine):
            raise
