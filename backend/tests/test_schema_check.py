"""Unit tests for app.core.schema_check - Stage 2's fail-clearly startup
guard, replacing the old (Stage 1B) schema_compat.py shim. See
test_migrations_fresh_install.py and test_migrations_legacy_upgrade.py for
the same guard exercised through the real Alembic-migrated path end to end;
these tests isolate ensure_database_ready()'s own logic against a few
specific schema shapes.
"""

import app.main as main_module
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.core.schema_check import DatabaseNotMigratedError, ensure_database_ready
from tests import stage2_db_helpers as h
from tests.test_migrations_legacy_upgrade import _LEGACY_SCHEMA_DDL

pytestmark = pytest.mark.skipif(
    not h.TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set - skipping PostgreSQL integration tests",
)


def test_ready_on_the_full_current_schema(db_engine):
    # db_engine (tests/conftest.py) already has the full current model set
    # via Base.metadata.create_all() - a valid stand-in for "fully migrated
    # to head" for this narrow check, since it uses the exact same metadata
    # (see test_migrations_drift.py for the guarantee that these two never
    # silently diverge).
    ensure_database_ready(db_engine)  # must not raise


@pytest.fixture()
def disposable_database():
    name = h.disposable_database_name("schemacheck")
    h.create_disposable_database(name)
    engine = create_engine(h.admin_url().set(database=name))
    try:
        yield engine
    finally:
        engine.dispose()
        h.drop_disposable_database(name)


def test_raises_on_a_completely_empty_database(disposable_database):
    with pytest.raises(DatabaseNotMigratedError, match="missing table"):
        ensure_database_ready(disposable_database)


def test_raises_on_the_legacy_pre_stage1a_shape(disposable_database):
    # Missing two whole tables (Stage 1A's and Stage 1B's) - the
    # "missing table(s)" branch fires before the service_id-specific check
    # ever runs, which is correct: naming the bigger problem first.
    with disposable_database.begin() as conn:
        conn.execute(text(_LEGACY_SCHEMA_DDL))

    with pytest.raises(DatabaseNotMigratedError, match="application_behavior_capabilities"):
        ensure_database_ready(disposable_database)


def test_raises_on_service_id_specifically_once_every_table_exists(disposable_database):
    # Every expected table present (including both Stage 1A/1B additions),
    # but service_id itself was never added - isolates the second check.
    with disposable_database.begin() as conn:
        conn.execute(text(_LEGACY_SCHEMA_DDL))
        conn.execute(
            text(
                "CREATE TABLE application_behavior_capabilities ("
                "id SERIAL PRIMARY KEY, "
                "application_id INTEGER NOT NULL UNIQUE REFERENCES applications(id) ON DELETE CASCADE, "
                "capability_hash VARCHAR(64) NOT NULL UNIQUE, "
                "used_at TIMESTAMPTZ, "
                "created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
                ")"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE application_idempotency_keys ("
                "id SERIAL PRIMARY KEY, "
                "idempotency_key_hash VARCHAR(64) NOT NULL UNIQUE, "
                "request_hash VARCHAR(64) NOT NULL, "
                "application_id INTEGER REFERENCES applications(id) ON DELETE CASCADE, "
                "created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
                ")"
            )
        )

    with pytest.raises(DatabaseNotMigratedError, match="service_id"):
        ensure_database_ready(disposable_database)


# ---------------------------------------------------------------------------
# MAJOR 2 correction: ensure_database_ready must reject a broken *current*
# (Alembic head) schema, not just an unmigrated/legacy one. Each test below
# starts from the real current schema (built via Base.metadata.create_all -
# exactly what test_ready_on_the_full_current_schema above already proves
# ensure_database_ready accepts cleanly) and introduces exactly one
# deliberate structural defect at the database level, then asserts startup
# is refused.
# ---------------------------------------------------------------------------


@pytest.fixture()
def head_schema_engine():
    name = h.disposable_database_name("headcheck")
    h.create_disposable_database(name)
    engine = create_engine(h.admin_url().set(database=name))
    from app import models  # noqa: F401
    from app.core.database import Base

    Base.metadata.create_all(bind=engine)
    try:
        yield engine
    finally:
        engine.dispose()
        h.drop_disposable_database(name)


def test_raises_on_wrong_budget_type(head_schema_engine):
    with head_schema_engine.begin() as conn:
        conn.execute(text("ALTER TABLE applications ALTER COLUMN budget TYPE TEXT"))

    with pytest.raises(DatabaseNotMigratedError, match="budget"):
        ensure_database_ready(head_schema_engine)


def test_raises_on_wrong_budget_precision_or_scale(head_schema_engine):
    with head_schema_engine.begin() as conn:
        conn.execute(text("ALTER TABLE applications ALTER COLUMN budget TYPE NUMERIC(10, 2)"))

    with pytest.raises(DatabaseNotMigratedError, match="budget"):
        ensure_database_ready(head_schema_engine)


def test_raises_on_wrong_nullability(head_schema_engine):
    with head_schema_engine.begin() as conn:
        conn.execute(text("ALTER TABLE applications ALTER COLUMN contact_data DROP NOT NULL"))

    with pytest.raises(DatabaseNotMigratedError, match="contact_data"):
        ensure_database_ready(head_schema_engine)


def test_raises_on_a_missing_required_current_column(head_schema_engine):
    with head_schema_engine.begin() as conn:
        conn.execute(text("ALTER TABLE applications DROP COLUMN contact_data"))

    with pytest.raises(DatabaseNotMigratedError, match="contact_data"):
        ensure_database_ready(head_schema_engine)


def test_raises_on_a_missing_critical_foreign_key(head_schema_engine):
    with head_schema_engine.begin() as conn:
        conn.execute(
            text("ALTER TABLE behavior_metrics DROP CONSTRAINT behavior_metrics_application_id_fkey")
        )

    with pytest.raises(DatabaseNotMigratedError, match="FOREIGN KEY"):
        ensure_database_ready(head_schema_engine)


def test_raises_on_a_foreign_key_pointing_at_the_wrong_table(head_schema_engine):
    with head_schema_engine.begin() as conn:
        conn.execute(text("ALTER TABLE applications DROP CONSTRAINT applications_service_id_fkey"))
        conn.execute(
            text(
                "ALTER TABLE applications ADD CONSTRAINT applications_service_id_fkey "
                "FOREIGN KEY (service_id) REFERENCES admins(id)"
            )
        )

    with pytest.raises(DatabaseNotMigratedError, match="FOREIGN KEY"):
        ensure_database_ready(head_schema_engine)


def test_raises_on_a_missing_important_unique_constraint(head_schema_engine):
    with head_schema_engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE application_behavior_capabilities "
                "DROP CONSTRAINT application_behavior_capabilities_capability_hash_key"
            )
        )

    with pytest.raises(DatabaseNotMigratedError, match="UNIQUE"):
        ensure_database_ready(head_schema_engine)


def test_raises_on_a_missing_current_stage_table(head_schema_engine):
    with head_schema_engine.begin() as conn:
        conn.execute(text("DROP TABLE application_idempotency_keys"))

    with pytest.raises(DatabaseNotMigratedError, match="application_idempotency_keys"):
        ensure_database_ready(head_schema_engine)


# ---------------------------------------------------------------------------
# MAJOR 2/3 correction: ensure_database_ready must also reject a *current*
# head schema whose service_id FK silently points at a same-named table in
# another schema, or whose delete action drifted away from PostgreSQL's
# default NO ACTION - both proven through the real FastAPI lifespan (app.
# main's startup guard), not just a direct ensure_database_ready() call,
# since a unit-level pass alone wouldn't prove the actual running app
# refuses to start. main_module.engine is swapped for the duration of each
# test and restored in `finally`, exactly like test_migrations_legacy_
# upgrade.py's end-to-end proof does.
# ---------------------------------------------------------------------------


def test_clean_head_schema_passes_the_real_fastapi_lifespan(head_schema_engine):
    """Positive control: the correct, untouched current schema - the same
    one test_ready_on_the_full_current_schema above already proves passes
    ensure_database_ready() directly - must also let the real app start and
    serve a request."""
    original_engine = main_module.engine
    main_module.engine = head_schema_engine
    try:
        with TestClient(main_module.app) as client:
            response = client.get("/api/health")
        assert response.status_code == 200
    finally:
        main_module.engine = original_engine


def test_real_lifespan_refuses_a_cross_schema_service_id_foreign_key(head_schema_engine):
    """Codex's exact PoC against current head: applications.service_id
    redirected to a same-named table (admin_settings) living in a different
    schema (shadow) - table/column names alone made the old FK comparison
    consider this an exact match."""
    with head_schema_engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA shadow"))
        conn.execute(text("CREATE TABLE shadow.admin_settings (id INTEGER PRIMARY KEY)"))
        conn.execute(text("ALTER TABLE applications DROP CONSTRAINT applications_service_id_fkey"))
        conn.execute(
            text(
                "ALTER TABLE applications ADD CONSTRAINT applications_service_id_fkey "
                "FOREIGN KEY (service_id) REFERENCES shadow.admin_settings(id)"
            )
        )

    original_engine = main_module.engine
    main_module.engine = head_schema_engine
    try:
        with pytest.raises(DatabaseNotMigratedError, match="FOREIGN KEY"):
            with TestClient(main_module.app):
                pass
    finally:
        main_module.engine = original_engine


def test_real_lifespan_refuses_a_destructive_cascade_on_service_id(head_schema_engine):
    """applications.service_id has no ON DELETE clause in app/models/
    application.py (PostgreSQL's default NO ACTION - a service with existing
    applications cannot be deleted at all). Codex's PoC changed it to CASCADE
    and the old startup guard still passed, silently turning "delete a
    service" into "delete every application that used it"."""
    with head_schema_engine.begin() as conn:
        conn.execute(text("ALTER TABLE applications DROP CONSTRAINT applications_service_id_fkey"))
        conn.execute(
            text(
                "ALTER TABLE applications ADD CONSTRAINT applications_service_id_fkey "
                "FOREIGN KEY (service_id) REFERENCES admin_settings(id) ON DELETE CASCADE"
            )
        )

    original_engine = main_module.engine
    main_module.engine = head_schema_engine
    try:
        with pytest.raises(DatabaseNotMigratedError, match="NO ACTION"):
            with TestClient(main_module.app):
                pass
    finally:
        main_module.engine = original_engine


def test_real_lifespan_refuses_a_set_null_action_on_service_id(head_schema_engine):
    """A different wrong action (not just CASCADE) must also be refused -
    proves the check compares against the expected NO ACTION specifically,
    not just against "not CASCADE"."""
    with head_schema_engine.begin() as conn:
        conn.execute(text("ALTER TABLE applications DROP CONSTRAINT applications_service_id_fkey"))
        conn.execute(
            text(
                "ALTER TABLE applications ADD CONSTRAINT applications_service_id_fkey "
                "FOREIGN KEY (service_id) REFERENCES admin_settings(id) ON DELETE SET NULL"
            )
        )

    original_engine = main_module.engine
    main_module.engine = head_schema_engine
    try:
        with pytest.raises(DatabaseNotMigratedError, match="NO ACTION"):
            with TestClient(main_module.app):
                pass
    finally:
        main_module.engine = original_engine
