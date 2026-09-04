"""Legacy production database adoption (Stage 2).

A database deployed before Stage 2 existed has no `alembic_version` table
and predates Stage 1A/1B (see backend/alembic/versions/0001_legacy_baseline.py's
docstring for exactly which commit this corresponds to). This module makes
adopting such a database into the Alembic-managed lifecycle a single,
reproducible, narrowly-scoped operation:

1. Verify (read-only, mutates nothing) that the connected database actually
   has the expected legacy shape - exactly the four legacy tables, exactly
   their expected columns (types, lengths/precision, nullability,
   server-side defaults) and constraints (PK/UNIQUE/FK, including the
   foreign key's referenced *schema* - a same-named table in another schema
   must never be mistaken for the real one), and *none* of the Stage 1A/1B
   additions or any other unexpected table (see app.core.schema_introspect
   and _LEGACY_TABLES below). Refuses to proceed on anything else - a
   database that merely resembles the legacy shape, rather than guessing.
   Each legacy SERIAL primary key (admins/admin_settings/applications/
   behavior_metrics.id) is verified against real PostgreSQL sequence
   semantics, not just "has some server-side default" - see ColumnSpec.
   serial_pk's docstring: a constant default, a detached/wrongly-owned
   sequence, or a sequence already behind the table's current max id are
   all refused rather than silently adopted (or, worse, silently repaired).
2. Reassign ownership of the four legacy tables (and, automatically, their
   owned SERIAL sequences - PostgreSQL moves a table's owned sequences
   along with `ALTER TABLE ... OWNER TO` since ownership changes are
   transitive for identity/serial sequences) to the migration/owner role,
   so that role can manage them going forward exactly like it manages
   tables it created itself. Requires the cluster bootstrap/admin
   credential - reassigning a table's ownership away from its current owner
   needs to *be* a superuser or a member of both the old and new owning
   roles, which a freshly adopted database's operator cannot assume.
3. Stamp `alembic_version` at the legacy baseline revision using Alembic's
   own `command.stamp()`, connected as the migration/owner role - never a
   raw hand-crafted INSERT. Ordinary `alembic upgrade head` (see
   backend/alembic/env.py) then applies 0002/0003 on top, exactly as it
   would starting from a real 0001 upgrade().

Idempotent: if `alembic_version` already exists, this is a no-op (already
adopted) rather than re-running verification against a database whose shape
has since moved on to Stage 1A/1B - see adopt_legacy_database()'s docstring.

Usage: `python -m app.db_admin.adopt_legacy` (all credentials read from the
environment - see the module-level `_config_from_env()` below). This is a
deliberate one-time manual operator action, not part of the routine
docker-compose startup flow (see docker-compose.yml).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

import psycopg
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import inspect
from sqlalchemy.engine import URL, Engine, create_engine

from app.core.schema_introspect import (
    APPLICATION_SCHEMA,
    ColumnSpec,
    ForeignKeySpec,
    TableSpec,
    describe_table_problems,
)
from app.db_admin.bootstrap_roles import RoleCollisionError, validate_distinct_roles

# Shared with app.core.schema_check/app.core.schema_introspect/
# app.db_admin.bootstrap_roles - see APPLICATION_SCHEMA's docstring. Aliased
# to the pre-existing local name so every other reference in this module
# stays unchanged.
_SCHEMA = APPLICATION_SCHEMA
_ALEMBIC_VERSION_TABLE = "alembic_version"
_LEGACY_BASELINE_REVISION = "0001_legacy_baseline"

# The exact legacy (pre-Stage-1A) schema, table by table - verified against
# `git show e93238b:backend/app/models/` and
# backend/alembic/versions/0001_legacy_baseline.py's upgrade(), which
# recreates this same shape for a fresh install. Column names, SQL types,
# lengths/precision/scale, nullability, server-side defaults (only
# created_at/updated_at and each SERIAL id actually have one - every other
# default in these models, e.g. is_active/time_on_page/clicked_buttons, is
# applied by SQLAlchemy at INSERT time, never by PostgreSQL itself) and the
# constraints that matter (PK, important UNIQUEs, the behavior_metrics ->
# applications FK - including that it references applications in *this*
# schema, not a same-named table elsewhere) are all checked - not just
# column presence - so a database that merely *resembles* the legacy shape
# (e.g. a manually hand-rolled schema with a subtly wrong column type or a
# missing constraint) is refused rather than silently accepted. Each id
# column is additionally serial_pk=True - see ColumnSpec.serial_pk and
# app.core.schema_introspect._serial_sequence_problems.
_LEGACY_TABLES: tuple[TableSpec, ...] = (
    TableSpec(
        name="admins",
        columns=(
            ColumnSpec("id", "INTEGER", nullable=False, server_default=True, serial_pk=True),
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
            ColumnSpec("id", "INTEGER", nullable=False, server_default=True, serial_pk=True),
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
            ColumnSpec("id", "INTEGER", nullable=False, server_default=True, serial_pk=True),
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
            ColumnSpec("interested_product", "VARCHAR", nullable=False, length=255),
            ColumnSpec("budget", "NUMERIC", nullable=False, precision=12, scale=2),
            ColumnSpec("preferred_contact_method", "VARCHAR", nullable=False, length=50),
            ColumnSpec("preferred_contact_time", "VARCHAR", nullable=False, length=100),
            ColumnSpec("comment", "TEXT", nullable=True),
            ColumnSpec("created_at", "TIMESTAMPTZ", nullable=False, server_default=True),
            ColumnSpec("updated_at", "TIMESTAMPTZ", nullable=False, server_default=True),
        ),
        primary_key=("id",),
        # No service_id (Stage 1B) column/FK yet - its presence is checked
        # explicitly below with a clearer, more specific message, but would
        # also be caught here as an unexpected column either way.
    ),
    TableSpec(
        name="behavior_metrics",
        columns=(
            ColumnSpec("id", "INTEGER", nullable=False, server_default=True, serial_pk=True),
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
)

_LEGACY_TABLE_NAMES = frozenset(spec.name for spec in _LEGACY_TABLES)


class LegacySchemaVerificationError(RuntimeError):
    """Raised when the connected database does not match the expected legacy
    (pre-Stage-1A) fingerprint closely enough to safely adopt. Never raised
    after any mutation - verification is fully read-only."""


class AlreadyAdoptedError(RuntimeError):
    """Raised only internally to signal the already-adopted short-circuit;
    callers should treat this the same as success (see adopt_legacy_database)."""


@dataclass(frozen=True)
class AdoptionConfig:
    host: str
    port: int
    database: str
    admin_user: str
    admin_password: str
    migration_role: str
    migration_password: str

    def __post_init__(self) -> None:
        # Same fail-closed role-collision check adopt_legacy.py's sibling
        # module (bootstrap_roles.py) applies to its own three roles -
        # applied here too since this dataclass independently carries the
        # cluster admin and migration/owner identities, and adopt_legacy_
        # database() reassigns table ownership between them (see
        # _reassign_ownership below). Raised at construction time, before
        # any connection is opened.
        validate_distinct_roles(
            {
                "cluster bootstrap/admin": self.admin_user,
                "migration/owner": self.migration_role,
            }
        )


def _connect_admin(config: AdoptionConfig) -> psycopg.Connection:
    return psycopg.connect(
        host=config.host,
        port=config.port,
        dbname=config.database,
        user=config.admin_user,
        password=config.admin_password,
        autocommit=True,
    )


def _table_exists(conn: psycopg.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM pg_catalog.pg_tables WHERE schemaname = %s AND tablename = %s",
        (_SCHEMA, table),
    ).fetchone()
    return row is not None


def _admin_url(config: AdoptionConfig) -> str:
    return URL.create(
        drivername="postgresql+psycopg",
        username=config.admin_user,
        password=config.admin_password,
        host=config.host,
        port=config.port,
        database=config.database,
    ).render_as_string(hide_password=False)


def verify_legacy_fingerprint(engine: Engine) -> None:
    """Read-only. Raises LegacySchemaVerificationError with a clear reason
    on any mismatch; returns normally iff the database looks exactly like
    the expected legacy (pre-Stage-1A) baseline - table existence, exact
    column set, column types/lengths/precision/nullability/server-defaults,
    and the PK/UNIQUE/FK constraints that matter (see _LEGACY_TABLES).
    Never mutates anything - safe to call as many times as desired."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names(schema=_SCHEMA))

    problems: list[str] = []

    missing_tables = _LEGACY_TABLE_NAMES - existing_tables
    for table in sorted(missing_tables):
        problems.append(f"expected legacy table {table!r} does not exist")

    # Any table beyond the exact legacy four - whether a Stage 1A/1B
    # addition, a partial/manual migration, or an unrelated extra table -
    # means this database's shape has already moved on (or was never
    # exactly the legacy baseline to begin with); blindly stamping it at
    # the legacy baseline would make 0002/0003 try to recreate objects that
    # already exist, or silently adopt a database nobody actually verified.
    unexpected_tables = existing_tables - _LEGACY_TABLE_NAMES
    if unexpected_tables:
        problems.append(
            f"unexpected table(s) present {sorted(unexpected_tables)!r} - this database does not "
            "match the expected legacy (pre-Stage-1A) schema exactly (already past the legacy "
            "baseline, a partial/manual migration, or an incompatible extra application table)"
        )

    for spec in _LEGACY_TABLES:
        if spec.name in missing_tables:
            continue  # already reported above; nothing meaningful to compare
        problems.extend(describe_table_problems(inspector, spec))

    if problems:
        raise LegacySchemaVerificationError(
            "Refusing to adopt: this database does not match the expected legacy "
            "(pre-Stage-1A) schema fingerprint:\n- " + "\n- ".join(problems)
        )


def _reassign_ownership(conn: psycopg.Connection, migration_role: str) -> None:
    """ALTER TABLE ... OWNER TO for every legacy table, explicitly schema-
    qualified to _SCHEMA (never relying on the connection's search_path) so
    this can never reassign ownership of a same-named table sitting in some
    other schema (e.g. one named after the connecting admin role) while
    leaving the real public table untouched. PostgreSQL transitively
    reassigns each table's owned SERIAL sequence along with it, so sequences
    need no separate statement here."""
    for table in _LEGACY_TABLE_NAMES:
        conn.execute(
            sql.SQL("ALTER TABLE {}.{} OWNER TO {}").format(
                sql.Identifier(_SCHEMA), sql.Identifier(table), sql.Identifier(migration_role)
            )
        )


def _migration_url(config: AdoptionConfig) -> str:
    return URL.create(
        drivername="postgresql+psycopg",
        username=config.migration_role,
        password=config.migration_password,
        host=config.host,
        port=config.port,
        database=config.database,
    ).render_as_string(hide_password=False)


def _alembic_config(config: AdoptionConfig) -> Config:
    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cfg = Config(os.path.join(backend_dir, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(backend_dir, "alembic"))
    cfg.set_main_option("sqlalchemy.url", _migration_url(config))
    return cfg


def adopt_legacy_database(config: AdoptionConfig) -> str:
    """Run the full adoption procedure. Returns a short human-readable
    status message (never containing a password). Idempotent: if
    `alembic_version` already exists, returns immediately without touching
    anything else - this is treated as "already adopted", not an error."""
    with _connect_admin(config) as conn:
        if _table_exists(conn, _ALEMBIC_VERSION_TABLE):
            return "Database is already adopted (alembic_version already exists) - nothing to do."

    # Verification runs on its own engine/connection, entirely before the
    # ownership-mutating connection below is even opened - not just
    # logically read-only, but temporally separated from any mutation.
    verify_engine = create_engine(_admin_url(config))
    try:
        verify_legacy_fingerprint(verify_engine)
    finally:
        verify_engine.dispose()

    with _connect_admin(config) as conn:
        _reassign_ownership(conn, config.migration_role)

    command.stamp(_alembic_config(config), _LEGACY_BASELINE_REVISION)
    return (
        f"Legacy database verified and adopted: ownership reassigned to "
        f"{config.migration_role!r}, alembic_version stamped at "
        f"{_LEGACY_BASELINE_REVISION!r}. Run `alembic upgrade head` next."
    )


def _config_from_env() -> AdoptionConfig:
    return AdoptionConfig(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        database=os.environ["POSTGRES_DB"],
        admin_user=os.environ["POSTGRES_USER"],
        admin_password=os.environ["POSTGRES_PASSWORD"],
        migration_role=os.environ["MIGRATION_DB_USER"],
        migration_password=os.environ["MIGRATION_DB_PASSWORD"],
    )


def main(argv: list[str] | None = None) -> int:
    del argv
    try:
        config = _config_from_env()
    except KeyError as exc:
        print(f"adopt_legacy: missing required environment variable {exc}", file=sys.stderr)
        return 1
    except RoleCollisionError as exc:
        print(f"adopt_legacy: {exc}", file=sys.stderr)
        return 1

    try:
        message = adopt_legacy_database(config)
    except LegacySchemaVerificationError as exc:
        print(f"adopt_legacy: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - never print str(exc): a DBAPI error can
        # echo back the failing statement/connection string, which may include a password.
        print(f"adopt_legacy: failed with an unexpected {type(exc).__name__}.", file=sys.stderr)
        return 1

    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
