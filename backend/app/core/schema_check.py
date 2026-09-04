"""Stage 2: fail-clearly startup guard, replacing the Stage 1B temporary
schema-mutation compatibility shim (formerly app/core/schema_compat.py).

Alembic migrations (see backend/alembic/) are now the only schema evolution
mechanism. FastAPI startup (app/main.py's lifespan) must never CREATE, ALTER,
or otherwise mutate the schema - a database that hasn't been migrated yet
(or is missing a Stage 1A/1B addition, or has drifted into some other
critically-wrong shape) must make the backend refuse to start with a clear,
actionable error instead of silently patching itself up or serving requests
against a schema it doesn't actually have.

This is a read-only reflection check (SQLAlchemy's Inspector), not an
Alembic-version check - it never queries alembic_version, which the runtime
application role deliberately has zero grants on (see
app/db_admin/bootstrap_roles.py and tests/test_db_role_privileges.py).
Reflection itself needs no extra privilege beyond what the runtime role
already needs for CRUD: PostgreSQL's information_schema/pg_catalog only
reveal objects the connected role already has some privilege on, which for
the runtime role's app tables is exactly the SELECT/INSERT/UPDATE/DELETE
grants it holds anyway.

The signature checked below is deliberately explicit and fixed (see
_CURRENT_TABLES) rather than a full Alembic autogenerate/diff operation -
this is a startup guard, not a migration tool, and must stay cheap, never
touch alembic_version, and never require the migration credential. See
app/core/schema_introspect.py for the comparison logic this shares with
app/db_admin/adopt_legacy.py's legacy fingerprint check.
"""

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.core.schema_introspect import (
    APPLICATION_SCHEMA,
    CHECK_NO_ACTION,
    ColumnSpec,
    ForeignKeySpec,
    TableSpec,
    describe_table_problems,
)

# The full current (Stage 1B head) schema, table by table - verified against
# app/models/*.py and backend/alembic/versions/0001..0003. Kept as a plain
# literal rather than derived from Base.metadata.tables so this check still
# catches the case where metadata and the live migrations have drifted apart
# (see tests/test_migrations_drift.py for the dedicated drift check, and
# tests/test_schema_check.py for this module's own unit tests).
_CURRENT_TABLES: tuple[TableSpec, ...] = (
    TableSpec(
        name="admins",
        columns=(
            ColumnSpec("id", "INTEGER", nullable=False, server_default=True),
            ColumnSpec("username", "VARCHAR", nullable=False, length=150),
            ColumnSpec("password_hash", "VARCHAR", nullable=False, length=255),
            ColumnSpec("is_active", "BOOLEAN", nullable=False, server_default=False),
            ColumnSpec("created_at", "TIMESTAMPTZ", nullable=False, server_default=True),
            ColumnSpec("updated_at", "TIMESTAMPTZ", nullable=False, server_default=True),
        ),
        primary_key=("id",),
        unique_constraints=(frozenset({"username"}),),
    ),
    TableSpec(
        name="admin_settings",
        columns=(
            ColumnSpec("id", "INTEGER", nullable=False, server_default=True),
            ColumnSpec("service_name", "VARCHAR", nullable=False, length=255),
            ColumnSpec("budget_min", "NUMERIC", nullable=False, precision=12, scale=2),
            ColumnSpec("budget_max", "NUMERIC", nullable=False, precision=12, scale=2),
            ColumnSpec("description", "TEXT", nullable=True, server_default=False),
            ColumnSpec("is_active", "BOOLEAN", nullable=False, server_default=False),
            ColumnSpec("created_at", "TIMESTAMPTZ", nullable=False, server_default=True),
            ColumnSpec("updated_at", "TIMESTAMPTZ", nullable=False, server_default=True),
        ),
        primary_key=("id",),
    ),
    TableSpec(
        name="applications",
        columns=(
            ColumnSpec("id", "INTEGER", nullable=False, server_default=True),
            ColumnSpec("first_name", "VARCHAR", nullable=False, length=100),
            ColumnSpec("last_name", "VARCHAR", nullable=False, length=100),
            ColumnSpec("middle_name", "VARCHAR", nullable=True, length=100),
            ColumnSpec("contact_data", "VARCHAR", nullable=False, length=255),
            ColumnSpec("business_niche", "VARCHAR", nullable=False, length=255),
            ColumnSpec("company_size", "VARCHAR", nullable=False, length=50),
            ColumnSpec("business_info", "TEXT", nullable=False),
            ColumnSpec("task_scope", "TEXT", nullable=False),
            ColumnSpec("requester_role", "VARCHAR", nullable=False, length=50),
            ColumnSpec("business_size", "VARCHAR", nullable=False, length=50),
            ColumnSpec("need_scope", "TEXT", nullable=False),
            ColumnSpec("deadline", "VARCHAR", nullable=False, length=100),
            ColumnSpec("task_type", "VARCHAR", nullable=False, length=100),
            # Stage 1B: nullable at the DB level - see app/models/application.py.
            ColumnSpec("service_id", "INTEGER", nullable=True),
            ColumnSpec("interested_product", "VARCHAR", nullable=False, length=255),
            ColumnSpec("budget", "NUMERIC", nullable=False, precision=12, scale=2),
            ColumnSpec("preferred_contact_method", "VARCHAR", nullable=False, length=50),
            ColumnSpec("preferred_contact_time", "VARCHAR", nullable=False, length=100),
            ColumnSpec("comment", "TEXT", nullable=True),
            ColumnSpec("created_at", "TIMESTAMPTZ", nullable=False, server_default=True),
            ColumnSpec("updated_at", "TIMESTAMPTZ", nullable=False, server_default=True),
            # Stage 4: materialized copy of score_application(...).score,
            # maintained client-side (SQLAlchemy mapper event, never a
            # PostgreSQL server-side default) - see app/models/application.py.
            ColumnSpec("priority_score", "INTEGER", nullable=False, server_default=True),
        ),
        primary_key=("id",),
        foreign_keys=(
            # No ON DELETE clause in app/models/application.py - PostgreSQL's
            # default is NO ACTION (a service with existing applications
            # cannot be deleted at all, see app/crud/admin_setting.py).
            # CHECK_NO_ACTION (not the bare presence-only None) so a
            # destructive change to e.g. ON DELETE CASCADE is caught, not
            # silently ignored - see MAJOR 3 in the Stage 2 correction pass.
            ForeignKeySpec(
                columns=("service_id",),
                ref_table="admin_settings",
                ref_columns=("id",),
                ondelete=CHECK_NO_ACTION,
            ),
        ),
    ),
    TableSpec(
        name="behavior_metrics",
        columns=(
            ColumnSpec("id", "INTEGER", nullable=False, server_default=True),
            ColumnSpec("application_id", "INTEGER", nullable=False),
            ColumnSpec("time_on_page", "INTEGER", nullable=False, server_default=False),
            ColumnSpec("clicked_buttons", "JSONB", nullable=False, server_default=False),
            ColumnSpec("cursor_hover_data", "JSONB", nullable=False, server_default=False),
            ColumnSpec("return_count", "INTEGER", nullable=False, server_default=False),
            ColumnSpec("created_at", "TIMESTAMPTZ", nullable=False, server_default=True),
            ColumnSpec("updated_at", "TIMESTAMPTZ", nullable=False, server_default=True),
        ),
        primary_key=("id",),
        unique_constraints=(frozenset({"application_id"}),),
        foreign_keys=(
            ForeignKeySpec(
                columns=("application_id",),
                ref_table="applications",
                ref_columns=("id",),
                ondelete="CASCADE",
            ),
        ),
    ),
    TableSpec(
        name="application_behavior_capabilities",
        columns=(
            ColumnSpec("id", "INTEGER", nullable=False, server_default=True),
            ColumnSpec("application_id", "INTEGER", nullable=False),
            ColumnSpec("capability_hash", "VARCHAR", nullable=False, length=64),
            ColumnSpec("used_at", "TIMESTAMPTZ", nullable=True, server_default=False),
            ColumnSpec("created_at", "TIMESTAMPTZ", nullable=False, server_default=True),
        ),
        primary_key=("id",),
        unique_constraints=(frozenset({"application_id"}), frozenset({"capability_hash"})),
        foreign_keys=(
            ForeignKeySpec(
                columns=("application_id",),
                ref_table="applications",
                ref_columns=("id",),
                ondelete="CASCADE",
            ),
        ),
    ),
    TableSpec(
        name="application_idempotency_keys",
        columns=(
            ColumnSpec("id", "INTEGER", nullable=False, server_default=True),
            ColumnSpec("idempotency_key_hash", "VARCHAR", nullable=False, length=64),
            ColumnSpec("request_hash", "VARCHAR", nullable=False, length=64),
            ColumnSpec("application_id", "INTEGER", nullable=True),
            ColumnSpec("created_at", "TIMESTAMPTZ", nullable=False, server_default=True),
        ),
        primary_key=("id",),
        unique_constraints=(frozenset({"idempotency_key_hash"}),),
        foreign_keys=(
            ForeignKeySpec(
                columns=("application_id",),
                ref_table="applications",
                ref_columns=("id",),
                ondelete="CASCADE",
            ),
        ),
    ),
)

_CURRENT_TABLE_NAMES = frozenset(spec.name for spec in _CURRENT_TABLES)


class DatabaseNotMigratedError(RuntimeError):
    """Raised when the connected database does not yet have the schema this
    application version requires. Never raised for anything this process
    could fix itself - the fix is always `alembic upgrade head`."""


def _ensure_current_schema_is_application_schema(engine: Engine) -> None:
    """Read-only. The reflection checks below all pass an explicit
    schema=APPLICATION_SCHEMA, so they can't be fooled by a same-named
    shadow schema - but every actual ORM query this application issues at
    runtime (app.models/*.py, via app.core.database's engine) is
    unqualified, and PostgreSQL resolves an unqualified name against
    whatever the connection's search_path puts first (by default "$user",
    public - i.e. a schema sharing the connected role's own name, if one
    happens to exist, silently wins over "public"). This is a second,
    independent guarantee that closes that gap: current_schema() is exactly
    the schema PostgreSQL itself would resolve an unqualified statement
    against, so refusing startup unless it is APPLICATION_SCHEMA proves the
    runtime connection cannot silently target a role-named or other
    non-public schema - not just that reflection was looking in the right
    place. A single SELECT; never mutates search_path or anything else."""
    with engine.connect() as conn:
        current_schema = conn.execute(text("SELECT current_schema()")).scalar()
    if current_schema != APPLICATION_SCHEMA:
        raise DatabaseNotMigratedError(
            "Refusing to start: this connection's effective default schema "
            f"(current_schema()) is {current_schema!r}, expected {APPLICATION_SCHEMA!r}. "
            "The connected role's search_path is resolving unqualified table names "
            "somewhere other than the public application schema (e.g. a schema "
            "sharing the role's own name via PostgreSQL's default \"$user\", public "
            "search_path) - fix the role's search_path, or remove the shadowing "
            "schema, before starting the backend."
        )


def ensure_database_ready(engine: Engine) -> None:
    """Raise DatabaseNotMigratedError unless the database already has the
    full current (Stage 1B) schema - not just the right tables/columns
    present, but their types, lengths/precision, nullability, PKs, the
    critical UNIQUE constraints, and the critical FKs (see _CURRENT_TABLES).
    Called once from app.main's lifespan, before the app starts serving
    requests. Never mutates anything."""
    _ensure_current_schema_is_application_schema(engine)

    inspector = inspect(engine)
    # Explicit schema= (rather than relying on the ambient search_path/
    # inspector.default_schema_name) so a same-named table sitting in some
    # other schema is never mistaken for the real one - see MAJOR 2 in the
    # Stage 2 correction pass, and app.core.schema_introspect.
    # describe_table_problems for the same explicit-schema treatment applied
    # to every column/constraint/FK check below.
    existing_tables = set(inspector.get_table_names(schema=APPLICATION_SCHEMA))
    missing_tables = _CURRENT_TABLE_NAMES - existing_tables
    if missing_tables:
        raise DatabaseNotMigratedError(
            "Database schema is out of date: missing table(s) "
            f"{sorted(missing_tables)!r}. Run `alembic upgrade head` "
            "(see backend/alembic) with the migration credential before "
            "starting the backend."
        )

    problems: list[str] = []
    for spec in _CURRENT_TABLES:
        problems.extend(describe_table_problems(inspector, spec))

    if problems:
        raise DatabaseNotMigratedError(
            "Database schema is out of date or does not match the schema this application "
            "version expects:\n- " + "\n- ".join(problems) + "\nRun `alembic upgrade head` "
            "(see backend/alembic) with the migration credential before starting the backend."
        )
