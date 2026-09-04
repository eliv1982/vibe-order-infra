"""Stage 2, mandatory least-privilege proof (see the Stage 2 spec, section 19).

Builds a fully migrated, fully grant-finalized disposable database (the same
sequence docker-compose.yml runs: db-roles-bootstrap -> db-migrate ->
db-roles-finalize) and proves the runtime role's exact privilege boundary
directly against PostgreSQL - not through the ORM, so nothing here can be
masked by an application-level check that happens to agree with itself.

Requires TEST_DATABASE_URL with CREATEDB+CREATEROLE rights - see
tests/stage2_db_helpers.py's module docstring.
"""

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

from app.db_admin.bootstrap_roles import bootstrap_roles
from tests import stage2_db_helpers as h

pytestmark = pytest.mark.skipif(
    not h.TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set - skipping PostgreSQL integration tests",
)


@pytest.fixture()
def finalized_stage2_database():
    name = h.disposable_database_name("privs")
    h.create_disposable_database(name)
    config = h.role_bootstrap_config(name)
    bootstrap_roles(config)  # db-roles-bootstrap
    command.upgrade(h.alembic_config_for(h.migration_database_url(name)), "head")  # db-migrate
    bootstrap_roles(config)  # db-roles-finalize
    try:
        yield h.Stage2Database(
            name=name, migration_url=h.migration_database_url(name), app_url=h.app_database_url(name)
        )
    finally:
        h.drop_disposable_database(name)


def _denied(conn, sql: str) -> None:
    with pytest.raises(ProgrammingError, match="permission denied|must be owner"):
        conn.execute(text(sql))
    conn.rollback()


def test_runtime_role_attributes_are_not_superuser_or_privileged(finalized_stage2_database):
    admin_engine = create_engine(h.admin_url().set(database="postgres"))
    try:
        with admin_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls "
                    "FROM pg_catalog.pg_roles WHERE rolname = :name"
                ),
                {"name": h.APP_ROLE},
            ).one()
        assert row.rolsuper is False
        assert row.rolcreatedb is False
        assert row.rolcreaterole is False
        assert row.rolbypassrls is False
    finally:
        admin_engine.dispose()


def test_runtime_role_full_crud_on_every_application_table(finalized_stage2_database):
    engine = create_engine(finalized_stage2_database.app_url)
    try:
        with engine.begin() as conn:
            setting_id = conn.execute(
                text(
                    "INSERT INTO admin_settings (service_name, budget_min, budget_max, is_active) "
                    "VALUES ('Privilege Test Service', 1.00, 1000.00, true) RETURNING id"
                )
            ).scalar_one()

            admin_id = conn.execute(
                text(
                    "INSERT INTO admins (username, password_hash, is_active) "
                    "VALUES ('privtest', 'not-a-real-hash', true) RETURNING id"
                )
            ).scalar_one()

            application_id = conn.execute(
                text(
                    "INSERT INTO applications ("
                    "first_name, last_name, contact_data, business_niche, company_size, "
                    "business_info, task_scope, requester_role, business_size, need_scope, "
                    "deadline, task_type, service_id, interested_product, budget, "
                    "preferred_contact_method, preferred_contact_time"
                    ") VALUES ("
                    "'Priv', 'Test', 'priv@example.com', 'n', 's', 'i', 'sc', 'r', 'bs', 'ne', "
                    "'d', 't', :sid, 'Privilege Test Service', 100.00, 'Телефон', 'Утро'"
                    ") RETURNING id"
                ),
                {"sid": setting_id},
            ).scalar_one()

            capability_id = conn.execute(
                text(
                    "INSERT INTO application_behavior_capabilities (application_id, capability_hash) "
                    "VALUES (:aid, 'a' || repeat('0', 63)) RETURNING id"
                ),
                {"aid": application_id},
            ).scalar_one()

            metric_id = conn.execute(
                text(
                    "INSERT INTO behavior_metrics (application_id, time_on_page, clicked_buttons, "
                    "cursor_hover_data, return_count) "
                    "VALUES (:aid, 10, '[]'::jsonb, '{}'::jsonb, 0) RETURNING id"
                ),
                {"aid": application_id},
            ).scalar_one()

            idem_id = conn.execute(
                text(
                    "INSERT INTO application_idempotency_keys (idempotency_key_hash, request_hash, application_id) "
                    "VALUES ('b' || repeat('0', 63), 'c' || repeat('0', 63), :aid) RETURNING id"
                ),
                {"aid": application_id},
            ).scalar_one()

            # UPDATE
            conn.execute(
                text("UPDATE admin_settings SET description = 'updated' WHERE id = :id"),
                {"id": setting_id},
            )
            conn.execute(
                text("UPDATE application_behavior_capabilities SET used_at = now() WHERE id = :id"),
                {"id": capability_id},
            )

            # SELECT
            assert conn.execute(
                text("SELECT description FROM admin_settings WHERE id = :id"), {"id": setting_id}
            ).scalar_one() == "updated"

            # DELETE (children before parents, respecting FKs)
            conn.execute(text("DELETE FROM application_idempotency_keys WHERE id = :id"), {"id": idem_id})
            conn.execute(text("DELETE FROM behavior_metrics WHERE id = :id"), {"id": metric_id})
            conn.execute(
                text("DELETE FROM application_behavior_capabilities WHERE id = :id"), {"id": capability_id}
            )
            conn.execute(text("DELETE FROM applications WHERE id = :id"), {"id": application_id})
            conn.execute(text("DELETE FROM admins WHERE id = :id"), {"id": admin_id})
            conn.execute(text("DELETE FROM admin_settings WHERE id = :id"), {"id": setting_id})
    finally:
        engine.dispose()


def test_runtime_role_cannot_perform_ddl(finalized_stage2_database):
    engine = create_engine(finalized_stage2_database.app_url)
    try:
        with engine.connect() as conn:
            _denied(conn, "CREATE TABLE stage2_forbidden_test (id INT)")
            _denied(conn, "ALTER TABLE applications ADD COLUMN forbidden_test INTEGER")
            _denied(conn, "ALTER TABLE applications ALTER COLUMN budget TYPE NUMERIC(20, 2)")
            _denied(conn, "DROP TABLE applications")
            _denied(conn, "DROP TABLE admin_settings")
            _denied(conn, "TRUNCATE applications")
            _denied(conn, "CREATE INDEX ON applications (first_name)")
    finally:
        engine.dispose()


def test_runtime_role_cannot_manage_roles_or_databases(finalized_stage2_database):
    engine = create_engine(finalized_stage2_database.app_url)
    try:
        with engine.connect() as conn:
            _denied(conn, "CREATE ROLE stage2_should_not_exist")
            _denied(conn, f"ALTER ROLE {h.MIGRATION_ROLE} WITH SUPERUSER")
    finally:
        engine.dispose()

    # CREATE DATABASE is a separate check: PostgreSQL refuses to run it
    # inside a transaction block at all (regardless of privilege), so it
    # needs its own autocommit connection to actually exercise the
    # permission check rather than tripping the transaction-block rule first.
    autocommit_engine = create_engine(finalized_stage2_database.app_url, isolation_level="AUTOCOMMIT")
    try:
        with autocommit_engine.connect() as conn:
            with pytest.raises(ProgrammingError, match="permission denied"):
                conn.execute(text("CREATE DATABASE stage2_should_not_exist"))
    finally:
        autocommit_engine.dispose()


def test_runtime_role_has_zero_access_to_alembic_version(finalized_stage2_database):
    engine = create_engine(finalized_stage2_database.app_url)
    try:
        with engine.connect() as conn:
            _denied(conn, "SELECT version_num FROM alembic_version")
            _denied(conn, "UPDATE alembic_version SET version_num = 'tampered'")
            _denied(conn, "DELETE FROM alembic_version")
            _denied(conn, "INSERT INTO alembic_version (version_num) VALUES ('tampered')")
    finally:
        engine.dispose()


def test_migration_role_can_still_apply_a_new_migration(finalized_stage2_database):
    """Contrast case: the migration role - unlike the runtime role - legitimately
    owns these tables and must be able to keep migrating them. Exercised here
    with a throwaway ALTER TABLE/DROP (not a real revision) rather than
    growing the real migration graph just for this test."""
    engine = create_engine(finalized_stage2_database.migration_url)
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE applications ADD COLUMN privilege_probe INTEGER"))
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE applications DROP COLUMN privilege_probe"))
    finally:
        engine.dispose()
