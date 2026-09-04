"""Stage 2, restore/migration verification (see the Stage 2 spec, section 13).

We will later move the real PostgreSQL data from Finland to a new server.
This exercises the workflow that move will actually follow, entirely
against disposable local databases:

    legacy dump/restore
    -> verify legacy schema/data
    -> adopt legacy Alembic baseline
    -> alembic upgrade head
    -> verify schema
    -> verify row counts/data
    -> backend smoke using runtime role

A real `pg_dump`/`pg_restore` round trip requires those client binaries on
PATH - not guaranteed on every developer machine (this repository's own dev
environment doesn't have them, only the `postgres` Docker image does). This
test uses them when available and is skipped with a clear reason otherwise;
either way, the data-preservation logic itself (steps 3-6 above) is already
exercised unconditionally, without any dependency on pg_dump/pg_restore, by
test_migrations_legacy_upgrade.py::
test_legacy_database_adoption_and_upgrade_end_to_end - that test is the
"minimum" fallback the Stage 2 spec calls for; this one is the "if
practical" real round trip on top of it.

Requires TEST_DATABASE_URL with CREATEDB+CREATEROLE rights - see
tests/stage2_db_helpers.py's module docstring.
"""

import shutil
import subprocess

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

from app.db_admin.adopt_legacy import adopt_legacy_database
from app.db_admin.bootstrap_roles import bootstrap_roles
from tests import stage2_db_helpers as h
from tests.test_migrations_legacy_upgrade import _seed_legacy_data

pytestmark = [
    pytest.mark.skipif(
        not h.TEST_DATABASE_URL,
        reason="TEST_DATABASE_URL is not set - skipping PostgreSQL integration tests",
    ),
    pytest.mark.skipif(
        shutil.which("pg_dump") is None or shutil.which("pg_restore") is None,
        reason="pg_dump/pg_restore not on PATH - skipping real dump/restore round trip "
        "(see test_migrations_legacy_upgrade.py for the unconditional data-preservation proof)",
    ),
]


def _run(args: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode == 0, f"{args[0]} failed: {result.stderr}"
    return result


@pytest.fixture()
def source_legacy_database():
    """The "Finland" side: a disposable database with the legacy schema and
    representative historical data, exactly like
    test_migrations_legacy_upgrade.py's fixture, but never itself
    adopted/migrated - it is only ever dumped from."""
    name = h.disposable_database_name("restore_src")
    h.create_disposable_database(name)
    engine = create_engine(h.admin_url().set(database=name))
    try:
        from app.core.security import hash_password

        _seed_legacy_data(engine, hash_password("RestoreSourcePassw0rd!123"))
    finally:
        engine.dispose()
    try:
        yield name
    finally:
        h.drop_disposable_database(name)


def test_pg_dump_restore_round_trip_then_adopt_and_migrate(source_legacy_database, tmp_path):
    source_url = h.admin_url().set(database=source_legacy_database)
    target_name = h.disposable_database_name("restore_dst")
    h.create_disposable_database(target_name)

    try:
        # 1. Dump the "Finland" source in custom (-Fc) format, the standard
        # pg_restore-compatible format for a full logical backup.
        dump_path = tmp_path / "legacy.dump"
        _run(
            [
                "pg_dump",
                "--format=custom",
                f"--file={dump_path}",
                source_url.render_as_string(hide_password=False),
            ]
        )
        assert dump_path.exists() and dump_path.stat().st_size > 0

        # 2. Restore into a completely separate disposable database - the
        # "new server" side. The source is never touched again after this.
        target_url = h.admin_url().set(database=target_name)
        _run(
            [
                "pg_restore",
                f"--dbname={target_url.render_as_string(hide_password=False)}",
                "--no-owner",
                "--no-privileges",
                str(dump_path),
            ]
        )

        # 3. Verify legacy schema/data survived the dump/restore round trip
        # itself, before any adoption/migration touches it.
        restored_engine = create_engine(target_url)
        try:
            with restored_engine.connect() as conn:
                assert conn.execute(text("SELECT count(*) FROM admins")).scalar_one() == 1
                assert conn.execute(text("SELECT count(*) FROM admin_settings")).scalar_one() == 1
                assert conn.execute(text("SELECT count(*) FROM applications")).scalar_one() == 2
                assert conn.execute(text("SELECT count(*) FROM behavior_metrics")).scalar_one() == 1
                interested_products = {
                    row[0]
                    for row in conn.execute(text("SELECT interested_product FROM applications"))
                }
                assert interested_products == {"Legacy Detailing Package"}
        finally:
            restored_engine.dispose()

        # 4. Adopt legacy Alembic baseline -> alembic upgrade head, against
        # the RESTORED copy - never the original source.
        config = h.role_bootstrap_config(target_name)
        bootstrap_roles(config)
        adopt_legacy_database(h.adoption_config(target_name))
        command.upgrade(h.alembic_config_for(h.migration_database_url(target_name)), "head")
        bootstrap_roles(config)  # finalize runtime grants

        # 5. Verify schema: Stage 1A/1B additions are present on the restored copy.
        migrated_engine = create_engine(h.migration_database_url(target_name))
        try:
            with migrated_engine.connect() as conn:
                version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                assert version == "0004_stage4_priority_score"
                # 6. Verify row counts/data are unchanged after migration too.
                assert conn.execute(text("SELECT count(*) FROM admins")).scalar_one() == 1
                assert conn.execute(text("SELECT count(*) FROM applications")).scalar_one() == 2
                assert (
                    conn.execute(
                        text("SELECT count(*) FROM applications WHERE service_id IS NULL")
                    ).scalar_one()
                    == 2
                )
        finally:
            migrated_engine.dispose()

        # 7. Backend smoke using the runtime role.
        app_engine = create_engine(h.app_database_url(target_name))
        try:
            from app.core.schema_check import ensure_database_ready

            ensure_database_ready(app_engine)
            with app_engine.connect() as conn:
                assert conn.execute(text("SELECT count(*) FROM applications")).scalar_one() == 2
        finally:
            app_engine.dispose()

        # 8. MINOR regression: restored PK sequences too, not just table
        # data - pg_dump/pg_restore preserves each sequence's current value,
        # but nothing previously asserted that explicitly. A restored
        # sequence must: permit new rows without colliding with the
        # historical ids it was restored with; be owned by the migration
        # role (ALTER TABLE ... OWNER TO reassigns a table's owned SERIAL
        # sequence transitively - see app/db_admin/adopt_legacy.py); and be
        # usable (nextval) by the runtime role without that role owning it
        # (see app/db_admin/bootstrap_roles.py).
        migration_engine = create_engine(h.migration_database_url(target_name))
        try:
            with migration_engine.connect() as conn:
                historical_max_id = conn.execute(text("SELECT max(id) FROM applications")).scalar_one()
                assert historical_max_id == 2

                sequence_owner = conn.execute(
                    text(
                        "SELECT sequenceowner FROM pg_sequences "
                        "WHERE schemaname = 'public' AND sequencename = 'applications_id_seq'"
                    )
                ).scalar_one()
                assert sequence_owner == h.MIGRATION_ROLE
        finally:
            migration_engine.dispose()

        app_engine = create_engine(h.app_database_url(target_name))
        try:
            with app_engine.begin() as conn:
                new_id = conn.execute(
                    text(
                        "INSERT INTO applications ("
                        "first_name, last_name, contact_data, business_niche, company_size, "
                        "business_info, task_scope, requester_role, business_size, need_scope, "
                        "deadline, task_type, interested_product, budget, "
                        "preferred_contact_method, preferred_contact_time"
                        ") VALUES ("
                        "'Restore', 'Sequence', 'restore-seq@example.com', 'n', 's', 'i', 'sc', "
                        "'r', 'bs', 'ne', 'd', 't', 'Legacy Detailing Package', 250.00, "
                        "'Телефон', 'Утро') RETURNING id"
                    )
                ).scalar_one()
            # The new id doesn't collide with (and comes after) restored
            # history - proof the sequence's current value survived the
            # dump/restore round trip, not just the table rows.
            assert new_id > historical_max_id

            # The INSERT above already proves the runtime role can use the
            # sequence (a SERIAL PK's nextval() needs USAGE); it must still
            # not own it - ALTER SEQUENCE requires ownership, not USAGE.
            with app_engine.connect() as conn:
                with pytest.raises(ProgrammingError, match="permission denied|must be owner"):
                    conn.execute(text("ALTER SEQUENCE applications_id_seq RESTART WITH 1000000"))
                conn.rollback()
        finally:
            app_engine.dispose()
    finally:
        h.drop_disposable_database(target_name)
