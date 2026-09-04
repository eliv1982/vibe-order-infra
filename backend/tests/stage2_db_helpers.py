"""Shared helpers for Stage 2 (database lifecycle) integration tests.

Every test that uses these helpers requires TEST_DATABASE_URL, exactly like
tests/test_api.py - skipped with an explicit reason if unset, never falling
back to production config (see tests/db_safety_guard.py). They additionally
assume that URL's user can CREATE DATABASE (already required by the
pre-existing disposable-database pattern these build on - see
tests/test_legacy_blank_service_api.py's predecessor) *and* CREATE ROLE -
i.e. is a local superuser/admin account. That is the normal shape of a
disposable local/CI test PostgreSQL instance and never true of any real
production credential; it is exactly the "cluster bootstrap/admin"
credential app/db_admin/bootstrap_roles.py and app/db_admin/adopt_legacy.py
are designed to require.

Every disposable database/role this module creates is torn down by the
fixtures that use it (see conftest-style fixtures in the test modules
themselves) - nothing here is left behind on success. Role names are fixed
(not per-test-unique) because CREATE ROLE is cluster-wide, not per-database,
and _ensure_role (app/db_admin/bootstrap_roles.py) is idempotent - reusing
the same two role names across every disposable test database in a run is
both safe and avoids leaking a growing set of orphaned roles into the
shared test cluster over many runs.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

from app.db_admin.adopt_legacy import AdoptionConfig
from app.db_admin.bootstrap_roles import RoleBootstrapConfig
from tests.db_safety_guard import get_test_database_url

TEST_DATABASE_URL = get_test_database_url()
PRODUCTION_POSTGRES_DB = os.environ.get("POSTGRES_DB")

_BACKEND_DIR = Path(__file__).resolve().parent.parent
ALEMBIC_INI_PATH = _BACKEND_DIR / "alembic.ini"
ALEMBIC_SCRIPT_LOCATION = _BACKEND_DIR / "alembic"

MIGRATION_ROLE = "stage2_test_migrator"
MIGRATION_ROLE_PASSWORD = "stage2_test_migrator_pw"
APP_ROLE = "stage2_test_app"
APP_ROLE_PASSWORD = "stage2_test_app_pw"


def admin_url():
    """The parsed TEST_DATABASE_URL itself - assumed to be a cluster
    superuser/admin connection (see module docstring)."""
    return make_url(TEST_DATABASE_URL)


def libpq_url(url: URL) -> str:
    """Render a SQLAlchemy URL for a libpq-based CLI tool (pg_dump/pg_restore/
    psql), not for SQLAlchemy's own create_engine().

    SQLAlchemy's drivername (e.g. "postgresql+psycopg", required by this
    project's own TEST_DATABASE_URL convention - see README's "Локальная
    разработка и тесты") is a SQLAlchemy-only convention. libpq's URI parser
    recognizes only a bare "postgresql://"/"postgres://" scheme; anything
    else doesn't match and it silently falls back to a local Unix-socket
    connection attempt instead of raising a clear parse error - see
    test_restore_roundtrip.py, which shells out to pg_dump/pg_restore
    directly and needs a URL those binaries can actually parse.

    The userinfo/host/port/database portion is left to URL.render_as_string
    itself (empirically verified against real libpq parsing - see
    test_libpq_url.py - to already produce something libpq accepts, space
    included, even though it isn't strictly RFC 3986). The query string is
    NOT: render_as_string() encodes it with urllib's quote_plus, which turns
    a space into "+" - correct for an HTML form/SQLAlchemy's own query-string
    convention, but libpq's URI parser does not decode "+" back to a space
    in the query part (unlike the rest of the URI, where a literal "+" and a
    literal space are just themselves either way - see test_libpq_url.py).
    A dump/restore target that happens to need a query parameter with a
    space in its value (e.g. options=-c search_path=...) would otherwise
    silently receive a corrupted value instead of a parse error. Rebuilt
    here with plain percent-encoding (quote(..., safe="")) instead.
    """
    plain = url.set(drivername="postgresql")
    query = plain.query
    if not query:
        return plain.render_as_string(hide_password=False)

    pairs: list[str] = []
    for key in sorted(query):
        values = query[key]
        if isinstance(values, str):
            values = (values,)
        for value in values:
            pairs.append(f"{quote(str(key), safe='')}={quote(str(value), safe='')}")

    base = plain.set(query={}).render_as_string(hide_password=False)
    return f"{base}?{'&'.join(pairs)}"


# Backward/internal alias kept for readability at call sites in this module.
_admin_url = admin_url


def disposable_database_name(suffix: str) -> str:
    base = _admin_url().database or "vibe_test"
    unique = uuid.uuid4().hex[:8]
    # Keeps the "test" marker db_safety_guard.py requires on every name
    # derived from it, and stays comfortably under PostgreSQL's 63-byte
    # identifier limit for any reasonable TEST_DATABASE_URL database name.
    return f"{base}_stage2_{suffix}_{unique}"


def create_disposable_database(database_name: str) -> None:
    admin_url = _admin_url().set(database="postgres")
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{database_name}"'))
    finally:
        engine.dispose()


def drop_disposable_database(database_name: str) -> None:
    admin_url = _admin_url().set(database="postgres")
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'))
    finally:
        engine.dispose()


def role_bootstrap_config(database_name: str) -> RoleBootstrapConfig:
    url = _admin_url()
    return RoleBootstrapConfig(
        host=url.host or "localhost",
        port=url.port or 5432,
        database=database_name,
        admin_user=url.username or "",
        admin_password=url.password or "",
        migration_role=MIGRATION_ROLE,
        migration_password=MIGRATION_ROLE_PASSWORD,
        app_role=APP_ROLE,
        app_password=APP_ROLE_PASSWORD,
    )


def adoption_config(database_name: str) -> AdoptionConfig:
    url = _admin_url()
    return AdoptionConfig(
        host=url.host or "localhost",
        port=url.port or 5432,
        database=database_name,
        admin_user=url.username or "",
        admin_password=url.password or "",
        migration_role=MIGRATION_ROLE,
        migration_password=MIGRATION_ROLE_PASSWORD,
    )


def migration_database_url(database_name: str) -> str:
    url = _admin_url()
    return URL.create(
        drivername="postgresql+psycopg",
        username=MIGRATION_ROLE,
        password=MIGRATION_ROLE_PASSWORD,
        host=url.host or "localhost",
        port=url.port or 5432,
        database=database_name,
    ).render_as_string(hide_password=False)


def app_database_url(database_name: str) -> str:
    url = _admin_url()
    return URL.create(
        drivername="postgresql+psycopg",
        username=APP_ROLE,
        password=APP_ROLE_PASSWORD,
        host=url.host or "localhost",
        port=url.port or 5432,
        database=database_name,
    ).render_as_string(hide_password=False)


def alembic_config_for(sqlalchemy_url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI_PATH))
    cfg.set_main_option("script_location", str(ALEMBIC_SCRIPT_LOCATION))
    cfg.set_main_option("sqlalchemy.url", sqlalchemy_url)
    return cfg


@dataclass(frozen=True)
class Stage2Database:
    """A disposable database plus the two Stage 2 roles bootstrapped on it,
    ready for `alembic upgrade head` (via migration_url) and app-level CRUD
    (via app_url)."""

    name: str
    migration_url: str
    app_url: str
