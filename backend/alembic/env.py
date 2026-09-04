"""Alembic environment: resolves the migration/owner DB connection and wires
up autogenerate/`alembic check` against this project's SQLAlchemy metadata.

Imports Base from app.core.db_base (not app.core.database, and never
app.core.config.Settings) - db_base.py has zero dependency on runtime
settings, so this module can run with only the migration credential in the
environment; it never requires APP_DB_USER/APP_DB_PASSWORD/JWT_SECRET_KEY to
be configured at all. Using the *runtime* (least-privileged) DATABASE URL
here would also just be the wrong credential for DDL (see the module
docstring in ../alembic.ini and app/db_admin/bootstrap_roles.py for the role
split).
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from app import models  # noqa: F401 - registers every mapped class on Base.metadata
from app.core.db_base import Base
from app.core.schema_introspect import APPLICATION_SCHEMA

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _migration_database_url() -> str:
    """Resolve the migration/owner connection URL.

    Priority:
    1. A URL already set on the Config object (tests/stage2_db_helpers.py
       does this via `config.set_main_option("sqlalchemy.url", ...)` to
       point Alembic at a disposable test database without touching any
       environment variable).
    2. MIGRATION_DB_USER/MIGRATION_DB_PASSWORD + POSTGRES_HOST/POSTGRES_PORT/
       POSTGRES_DB from the environment (the normal CLI-invoked case).

    Deliberately has no fallback to DATABASE_URL/APP_DB_USER - a missing
    migration credential must fail loudly, never silently borrow the
    runtime credential.
    """
    configured = config.get_main_option("sqlalchemy.url")
    if configured:
        return configured

    user = os.environ["MIGRATION_DB_USER"]
    password = os.environ["MIGRATION_DB_PASSWORD"]
    host = os.environ.get("POSTGRES_HOST", "postgres")
    port = int(os.environ.get("POSTGRES_PORT", "5432"))
    database = os.environ["POSTGRES_DB"]
    return URL.create(
        drivername="postgresql+psycopg",
        username=user,
        password=password,
        host=host,
        port=port,
        database=database,
    ).render_as_string(hide_password=False)


def run_migrations_offline() -> None:
    url = _migration_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # MAJOR correction: without this, `alembic check`/`--autogenerate`
        # compare column *type* drift only - a server_default added, removed
        # or changed directly against the live database (bypassing a
        # migration entirely) went completely undetected. See
        # tests/test_migrations_drift.py for the regression coverage this
        # backs (both directions: a genuine default drift must be caught,
        # and a clean migrated-head database must still report zero drift).
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Stage 2 MAJOR correction: op.create_table/alter (see backend/alembic/
    # versions/*.py) never schema-qualify - like the runtime engine (see
    # app.core.database), they rely entirely on the migration connection's
    # search_path to resolve to "public". Same explicit override as there
    # (see that module's matching comment for the full GUC-precedence
    # rationale) so `alembic upgrade`/`stamp`/`check` can never silently
    # create or inspect objects in a schema shadowing public (e.g. one named
    # after the migration role itself).
    connectable = create_engine(
        _migration_database_url(), connect_args={"options": f"-c search_path={APPLICATION_SCHEMA}"}
    )
    try:
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                # See run_migrations_offline()'s matching comment above -
                # both code paths must agree, since `alembic check` and
                # `alembic upgrade` normally run online, while offline mode
                # is only ever used to emit SQL scripts (--sql).
                compare_server_default=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
