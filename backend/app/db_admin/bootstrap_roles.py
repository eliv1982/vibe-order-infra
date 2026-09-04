"""Idempotent role bootstrap for the Stage 2 database lifecycle.

Establishes exactly two application-facing PostgreSQL roles, distinct from
the cluster bootstrap/admin role (the Postgres Docker image's own
POSTGRES_USER/POSTGRES_PASSWORD superuser, created by `postgres` itself at
container init):

- migration/owner role (MIGRATION_DB_USER/MIGRATION_DB_PASSWORD): the only
  role Alembic ever connects as (see backend/alembic/env.py). It gets
  CREATE+USAGE on the `public` schema, so it owns every table/sequence it
  creates via migrations - nothing here makes it a superuser.
- runtime application role (APP_DB_USER/APP_DB_PASSWORD): the only role
  app.core.database's engine ever connects as. It gets exactly
  SELECT/INSERT/UPDATE/DELETE on application tables and USAGE/SELECT/UPDATE
  on their sequences (needed for SERIAL primary keys) - no CREATE, ALTER, or
  DROP anywhere, and explicitly zero access to the `alembic_version` table.

Must be run with the cluster bootstrap/admin credential: creating a role,
and granting privileges over objects the connecting role doesn't itself
own, both require it (or an existing member of both roles, which a fresh
cluster never has). Safe and idempotent to run repeatedly, at any point
before or after `alembic upgrade head`, and against a database that already
has some or all application tables (e.g. right after legacy adoption - see
app/db_admin/adopt_legacy.py). Never prints a password.

Usage: `python -m app.db_admin.bootstrap_roles` (all credentials read from
the environment - see docker-compose.yml's db-roles-bootstrap and
db-roles-finalize services, which both run this same idempotent script).
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass

import psycopg
from psycopg import sql

from app.core.schema_introspect import APPLICATION_SCHEMA

# Shared with app.core.schema_check/app.core.schema_introspect/
# app.db_admin.adopt_legacy - see APPLICATION_SCHEMA's docstring. Aliased to
# the pre-existing local name so every other reference in this module stays
# unchanged.
_SCHEMA = APPLICATION_SCHEMA
_ALEMBIC_VERSION_TABLE = "alembic_version"

# Role/database identifiers here always come from this process's own
# environment (operator-controlled config, not attacker input), but they are
# still interpolated into DDL that can't use bind parameters for identifiers
# (CREATE ROLE/GRANT/... don't accept them) - validated defensively rather
# than trusted blindly.
_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_identifier(name: str, what: str) -> str:
    if not _SAFE_IDENTIFIER_RE.match(name):
        raise ValueError(
            f"{what} {name!r} is not a safe SQL identifier "
            "(expected: letters/digits/underscore, not starting with a digit)"
        )
    return name


class RoleCollisionError(RuntimeError):
    """Raised when two or more operationally distinct role identifiers
    (cluster/bootstrap admin, migration/owner, runtime application - see the
    module docstring) resolve to the same PostgreSQL role name.

    Raised from RoleBootstrapConfig/AdoptionConfig's own __post_init__ - i.e.
    at config *construction* time, before any database connection is even
    opened, let alone any CREATE/ALTER ROLE, GRANT, REVOKE or ownership
    change - so "before any mutation" holds by construction for every call
    path (the CLI entrypoints below, adopt_legacy.py, and every test), not
    merely by convention.
    """


def validate_distinct_roles(roles: dict[str, str]) -> None:
    """Raise RoleCollisionError unless every role name in `roles` (mapping a
    human-readable label to the resolved role identifier) is distinct from
    every other. Catches every pairwise equality, and therefore the
    all-equal case too."""
    seen: dict[str, str] = {}
    for label, name in roles.items():
        if name in seen:
            raise RoleCollisionError(
                f"the {seen[name]!r} role and the {label!r} role must be distinct PostgreSQL "
                f"roles, but both resolve to {name!r}. Stage 2's privilege separation requires "
                "the cluster/bootstrap admin, migration/owner and runtime application identities "
                "to be three distinct roles - collapsing any two would let a lower-privilege "
                "identity inherit rights (schema DDL, table ownership, or both) it must never have."
            )
        seen[name] = label


@dataclass(frozen=True)
class RoleBootstrapConfig:
    host: str
    port: int
    database: str
    admin_user: str
    admin_password: str
    migration_role: str
    migration_password: str
    app_role: str
    app_password: str

    def __post_init__(self) -> None:
        validate_distinct_roles(
            {
                "cluster bootstrap/admin": self.admin_user,
                "migration/owner": self.migration_role,
                "runtime application": self.app_role,
            }
        )


def _connect_admin(config: RoleBootstrapConfig) -> psycopg.Connection:
    conn = psycopg.connect(
        host=config.host,
        port=config.port,
        dbname=config.database,
        user=config.admin_user,
        password=config.admin_password,
        autocommit=True,
    )
    return conn


def _role_exists(conn: psycopg.Connection, role: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = %s", (role,)
    ).fetchone()
    return row is not None


def _ensure_role(conn: psycopg.Connection, role: str, password: str) -> None:
    """Create `role` if missing, then (re)assert its password and its safe,
    non-superuser/non-owner attributes - idempotent either way.

    The password is embedded via sql.Literal (safely quoted/escaped by
    psycopg), not a `%s` bind parameter: PostgreSQL's CREATE ROLE/ALTER ROLE
    are utility statements, not plannable DML, and their grammar does not
    accept a parameter placeholder in the PASSWORD clause at all - the
    server rejects it with a syntax error before this ever reaches a
    privilege check, regardless of role.
    """
    role = _validate_identifier(role, "role name")
    if not _role_exists(conn, role):
        conn.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role)))
    conn.execute(
        sql.SQL(
            "ALTER ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOREPLICATION NOBYPASSRLS PASSWORD {}"
        ).format(sql.Identifier(role), sql.Literal(password))
    )


def _grant_connect(conn: psycopg.Connection, role: str, database: str) -> None:
    conn.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            sql.Identifier(database), sql.Identifier(role)
        )
    )


def _harden_public_schema_defaults(conn: psycopg.Connection) -> None:
    """PostgreSQL 15+ already revokes CREATE on `public` from PUBLIC by
    default - this makes that explicit and version-independent, rather than
    relying on it. Idempotent (REVOKE of a privilege that isn't held is a
    no-op, not an error)."""
    conn.execute(sql.SQL("REVOKE CREATE ON SCHEMA {} FROM PUBLIC").format(sql.Identifier(_SCHEMA)))


def _grant_migration_role_schema_rights(conn: psycopg.Connection, migration_role: str) -> None:
    conn.execute(
        sql.SQL("GRANT CREATE, USAGE ON SCHEMA {} TO {}").format(
            sql.Identifier(_SCHEMA), sql.Identifier(migration_role)
        )
    )


def _grant_app_role_schema_usage(conn: psycopg.Connection, app_role: str) -> None:
    conn.execute(
        sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
            sql.Identifier(_SCHEMA), sql.Identifier(app_role)
        )
    )


def _set_default_privileges_for_future_objects(
    conn: psycopg.Connection, migration_role: str, app_role: str
) -> None:
    """Every table/sequence the migration role creates *from now on* (i.e.
    every future `alembic upgrade head`) automatically grants the runtime
    role exactly the CRUD rights it needs - no manual re-grant step is ever
    needed again for new migrations."""
    conn.execute(
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}"
        ).format(sql.Identifier(migration_role), sql.Identifier(_SCHEMA), sql.Identifier(app_role))
    )
    conn.execute(
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} "
            "GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {}"
        ).format(sql.Identifier(migration_role), sql.Identifier(_SCHEMA), sql.Identifier(app_role))
    )


def _grant_existing_objects(conn: psycopg.Connection, app_role: str) -> None:
    """Catch-up grant for objects that already existed *before*
    ALTER DEFAULT PRIVILEGES above took effect - a legacy-adopted database's
    pre-existing tables, or simply re-running this script after
    `alembic upgrade head` already created new tables. A no-op on a schema
    with no tables/sequences yet."""
    conn.execute(
        sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {} TO {}").format(
            sql.Identifier(_SCHEMA), sql.Identifier(app_role)
        )
    )
    conn.execute(
        sql.SQL("GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA {} TO {}").format(
            sql.Identifier(_SCHEMA), sql.Identifier(app_role)
        )
    )


def _revoke_alembic_version_access(conn: psycopg.Connection, app_role: str) -> None:
    """The runtime role must never be able to read or modify Alembic's own
    version-tracking table (app/core/schema_check.py's startup guard uses
    plain table/column reflection instead, precisely so this can be a flat
    zero rather than a narrower "SELECT-only" carve-out). Guarded by an
    existence check because a fresh database won't have this table yet the
    first time this script runs (before `alembic upgrade head`); harmless
    to skip in that case since the blanket grant above never touched a
    table that didn't exist."""
    row = conn.execute(
        "SELECT 1 FROM pg_catalog.pg_tables WHERE schemaname = %s AND tablename = %s",
        (_SCHEMA, _ALEMBIC_VERSION_TABLE),
    ).fetchone()
    if row is None:
        return
    conn.execute(
        sql.SQL("REVOKE ALL ON {}.{} FROM {}").format(
            sql.Identifier(_SCHEMA), sql.Identifier(_ALEMBIC_VERSION_TABLE), sql.Identifier(app_role)
        )
    )


def bootstrap_roles(config: RoleBootstrapConfig) -> None:
    migration_role = _validate_identifier(config.migration_role, "migration role")
    app_role = _validate_identifier(config.app_role, "app role")
    _validate_identifier(config.database, "database name")

    with _connect_admin(config) as conn:
        _ensure_role(conn, migration_role, config.migration_password)
        _ensure_role(conn, app_role, config.app_password)

        _grant_connect(conn, migration_role, config.database)
        _grant_connect(conn, app_role, config.database)

        _harden_public_schema_defaults(conn)
        _grant_migration_role_schema_rights(conn, migration_role)
        _grant_app_role_schema_usage(conn, app_role)

        _set_default_privileges_for_future_objects(conn, migration_role, app_role)
        _grant_existing_objects(conn, app_role)
        _revoke_alembic_version_access(conn, app_role)


def _config_from_env() -> RoleBootstrapConfig:
    return RoleBootstrapConfig(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        database=os.environ["POSTGRES_DB"],
        admin_user=os.environ["POSTGRES_USER"],
        admin_password=os.environ["POSTGRES_PASSWORD"],
        migration_role=os.environ["MIGRATION_DB_USER"],
        migration_password=os.environ["MIGRATION_DB_PASSWORD"],
        app_role=os.environ["APP_DB_USER"],
        app_password=os.environ["APP_DB_PASSWORD"],
    )


def main(argv: list[str] | None = None) -> int:
    del argv  # no CLI flags today - configuration is entirely environment-driven
    try:
        config = _config_from_env()
    except KeyError as exc:
        print(f"bootstrap_roles: missing required environment variable {exc}", file=sys.stderr)
        return 1
    except RoleCollisionError as exc:
        print(f"bootstrap_roles: {exc}", file=sys.stderr)
        return 1

    try:
        bootstrap_roles(config)
    except Exception as exc:  # noqa: BLE001 - deliberately never prints str(exc): a DBAPI
        # error can echo back the failing statement, which for ALTER ROLE ... PASSWORD
        # would be the plaintext password. Only the exception's type name is safe.
        print(f"bootstrap_roles: failed with an unexpected {type(exc).__name__}.", file=sys.stderr)
        return 1

    print(
        "Database roles bootstrapped/synced successfully "
        f"(migration role={config.migration_role!r}, app role={config.app_role!r})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
