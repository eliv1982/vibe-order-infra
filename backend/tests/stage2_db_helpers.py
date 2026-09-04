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
