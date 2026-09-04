"""Stage 2, mandatory fresh-install proof (see the Stage 2 spec, section 11).

Builds a genuinely empty disposable PostgreSQL database, bootstraps the two
Stage 2 roles on it, runs the real `alembic upgrade head` (via Alembic's own
command API, not a reimplementation), and proves:

1. the database started empty;
2. the migration/runtime roles exist;
3. `alembic upgrade head` succeeds;
4. Alembic's version state is at head;
5. every expected table exists;
6. expected columns/constraints/FKs exist and match across every migration
   path (see test_migrations_drift.py for the metadata-vs-head convergence
   check that backs this up more rigorously);
7. the backend can start (app.core.schema_check.ensure_database_ready) using
   only the runtime role;
8. normal CRUD works through the runtime role;
9. re-running the migration is a no-op;
10. the runtime role cannot perform DDL.

Requires TEST_DATABASE_URL with CREATEDB+CREATEROLE rights - see
tests/stage2_db_helpers.py's module docstring.
"""

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.core.schema_check import ensure_database_ready
from app.db_admin.bootstrap_roles import bootstrap_roles
from tests import stage2_db_helpers as h

pytestmark = pytest.mark.skipif(
    not h.TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set - skipping PostgreSQL integration tests",
)

_EXPECTED_TABLES = {
    "admins",
    "admin_settings",
    "applications",
    "behavior_metrics",
    "application_behavior_capabilities",
    "application_idempotency_keys",
}


@pytest.fixture()
def fresh_stage2_database():
    name = h.disposable_database_name("fresh")
    h.create_disposable_database(name)
    config = h.role_bootstrap_config(name)
    try:
        # Mirrors docker-compose.yml's db-roles-bootstrap step: roles +
        # default privileges established BEFORE any migration runs.
        bootstrap_roles(config)
        yield h.Stage2Database(
            name=name, migration_url=h.migration_database_url(name), app_url=h.app_database_url(name)
        )
    finally:
        h.drop_disposable_database(name)


def test_fresh_database_starts_empty(fresh_stage2_database):
    engine = create_engine(fresh_stage2_database.migration_url)
    try:
        assert inspect(engine).get_table_names() == []
    finally:
        engine.dispose()


def test_migration_and_runtime_roles_exist(fresh_stage2_database):
    maintenance_url = h.admin_url().set(database="postgres")
    engine = create_engine(maintenance_url)
    try:
        with engine.connect() as conn:
            roles = {
                row[0]
                for row in conn.execute(
                    text("SELECT rolname FROM pg_catalog.pg_roles WHERE rolname = ANY(:names)"),
                    {"names": [h.MIGRATION_ROLE, h.APP_ROLE]},
                )
            }
        assert roles == {h.MIGRATION_ROLE, h.APP_ROLE}
    finally:
        engine.dispose()


def test_fresh_install_reaches_head_with_full_schema(fresh_stage2_database):
    cfg = h.alembic_config_for(fresh_stage2_database.migration_url)

    # 3. `alembic upgrade head` succeeds.
    command.upgrade(cfg, "head")

    engine = create_engine(fresh_stage2_database.migration_url)
    try:
        # 4. Alembic version is at head.
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert version == "0003_stage1b_service_idemp"

        # 5. Expected tables exist.
        inspector = inspect(engine)
        assert _EXPECTED_TABLES <= set(inspector.get_table_names())

        # 6. Expected columns/constraints/FKs.
        application_columns = {c["name"] for c in inspector.get_columns("applications")}
        assert "service_id" in application_columns

        fk_names = {fk["name"] for fk in inspector.get_foreign_keys("applications")}
        assert "applications_service_id_fkey" in fk_names

        capability_uniques = {uc["name"] for uc in inspector.get_unique_constraints(
            "application_behavior_capabilities"
        )}
        assert "application_behavior_capabilities_application_id_key" in capability_uniques
        assert "application_behavior_capabilities_capability_hash_key" in capability_uniques

        idempotency_uniques = {
            uc["name"] for uc in inspector.get_unique_constraints("application_idempotency_keys")
        }
        assert "application_idempotency_keys_idempotency_key_hash_key" in idempotency_uniques
    finally:
        engine.dispose()

    # 9. Re-running the migration is a no-op (no error, version unchanged).
    command.upgrade(cfg, "head")
    engine = create_engine(fresh_stage2_database.migration_url)
    try:
        with engine.connect() as conn:
            version_again = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert version_again == "0003_stage1b_service_idemp"
    finally:
        engine.dispose()


def test_backend_starts_and_operates_with_runtime_role(fresh_stage2_database):
    cfg = h.alembic_config_for(fresh_stage2_database.migration_url)
    command.upgrade(cfg, "head")
    # Mirrors docker-compose.yml's db-roles-finalize step: catches the
    # tables/sequences just created and revokes alembic_version access.
    bootstrap_roles(h.role_bootstrap_config(fresh_stage2_database.name))

    app_engine = create_engine(fresh_stage2_database.app_url)
    try:
        # 7. Backend can start (the real startup guard) using the runtime role.
        ensure_database_ready(app_engine)

        # 8. Normal application operations work using the runtime role.
        with Session(app_engine) as session:
            setting_id = session.execute(
                text(
                    "INSERT INTO admin_settings (service_name, budget_min, budget_max, is_active) "
                    "VALUES ('Fresh Install Service', 10.00, 1000.00, true) RETURNING id"
                )
            ).scalar_one()
            session.commit()

            application_id = session.execute(
                text(
                    "INSERT INTO applications ("
                    "first_name, last_name, contact_data, business_niche, company_size, "
                    "business_info, task_scope, requester_role, business_size, need_scope, "
                    "deadline, task_type, service_id, interested_product, budget, "
                    "preferred_contact_method, preferred_contact_time"
                    ") VALUES ("
                    "'Fresh', 'Install', 'fresh@example.com', 'niche', 'size', 'info', 'scope', "
                    "'role', 'bsize', 'need', 'deadline', 'type', :service_id, 'Fresh Install Service', "
                    "500.00, 'Телефон', 'Утро'"
                    ") RETURNING id"
                ),
                {"service_id": setting_id},
            ).scalar_one()
            session.commit()

            read_back = session.execute(
                text("SELECT service_id FROM applications WHERE id = :id"), {"id": application_id}
            ).scalar_one()
            assert read_back == setting_id

            session.execute(text("DELETE FROM applications WHERE id = :id"), {"id": application_id})
            session.execute(text("DELETE FROM admin_settings WHERE id = :id"), {"id": setting_id})
            session.commit()

        # 10. Runtime role cannot perform DDL.
        with app_engine.connect() as conn:
            with pytest.raises(ProgrammingError, match="permission denied"):
                conn.execute(text("CREATE TABLE stage2_forbidden_test (id INT)"))
            conn.rollback()
            with pytest.raises(ProgrammingError, match="must be owner"):
                conn.execute(text("ALTER TABLE applications ADD COLUMN forbidden_test INTEGER"))
            conn.rollback()
            with pytest.raises(ProgrammingError, match="must be owner"):
                conn.execute(text("DROP TABLE applications"))
            conn.rollback()

        # Runtime role has zero access (not even SELECT) to alembic_version.
        with app_engine.connect() as conn:
            with pytest.raises(ProgrammingError, match="permission denied"):
                conn.execute(text("SELECT version_num FROM alembic_version"))
            conn.rollback()
    finally:
        app_engine.dispose()
