"""Stage 2, MAJOR 3 correction: role-name collisions must be rejected before
any mutation (see the Stage 2 spec, sections 6 and 12).

app.db_admin.bootstrap_roles.RoleBootstrapConfig and
app.db_admin.adopt_legacy.AdoptionConfig both validate that every
operationally distinct role identifier they carry (cluster/bootstrap admin,
migration/owner, runtime application) is pairwise distinct - in their own
__post_init__, i.e. at construction time, before any database connection is
ever opened. Every combination of collision is exercised here, together with
a live-database proof that an attempted collision changes nothing: no role
is created/altered, no grant is issued, and an already-established
privilege posture (the runtime role's DDL-denied boundary) survives an
attempted colliding re-bootstrap untouched.

Requires TEST_DATABASE_URL with CREATEDB+CREATEROLE rights - see
tests/stage2_db_helpers.py's module docstring.
"""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

from app.db_admin.adopt_legacy import AdoptionConfig
from app.db_admin.bootstrap_roles import RoleBootstrapConfig, RoleCollisionError, bootstrap_roles
from tests import stage2_db_helpers as h

pytestmark = pytest.mark.skipif(
    not h.TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set - skipping PostgreSQL integration tests",
)


def _bootstrap_kwargs(database_name: str) -> dict:
    url = h.admin_url()
    return dict(
        host=url.host or "localhost",
        port=url.port or 5432,
        database=database_name,
        admin_user=url.username or "",
        admin_password=url.password or "",
        migration_role=h.MIGRATION_ROLE,
        migration_password=h.MIGRATION_ROLE_PASSWORD,
        app_role=h.APP_ROLE,
        app_password=h.APP_ROLE_PASSWORD,
    )


@pytest.mark.parametrize(
    "collision",
    ["cluster_equals_migration", "cluster_equals_runtime", "migration_equals_runtime", "all_three_equal"],
)
def test_role_bootstrap_config_rejects_every_collision_combination(collision):
    kwargs = _bootstrap_kwargs("irrelevant_db_name_test")
    if collision == "cluster_equals_migration":
        kwargs["migration_role"] = kwargs["admin_user"]
    elif collision == "cluster_equals_runtime":
        kwargs["app_role"] = kwargs["admin_user"]
    elif collision == "migration_equals_runtime":
        kwargs["app_role"] = kwargs["migration_role"]
    elif collision == "all_three_equal":
        kwargs["migration_role"] = kwargs["admin_user"]
        kwargs["app_role"] = kwargs["admin_user"]

    with pytest.raises(RoleCollisionError):
        RoleBootstrapConfig(**kwargs)


def test_role_bootstrap_config_accepts_three_distinct_roles():
    RoleBootstrapConfig(**_bootstrap_kwargs("irrelevant_db_name_test"))  # must not raise


def test_adoption_config_rejects_cluster_equals_migration_collision():
    url = h.admin_url()
    with pytest.raises(RoleCollisionError):
        AdoptionConfig(
            host=url.host or "localhost",
            port=url.port or 5432,
            database="irrelevant_db_name_test",
            admin_user=url.username or "",
            admin_password=url.password or "",
            migration_role=url.username or "",  # collision: same as admin_user
            migration_password="whatever",
        )


def test_adoption_config_accepts_distinct_roles():
    url = h.admin_url()
    AdoptionConfig(
        host=url.host or "localhost",
        port=url.port or 5432,
        database="irrelevant_db_name_test",
        admin_user=url.username or "",
        admin_password=url.password or "",
        migration_role=h.MIGRATION_ROLE,
        migration_password=h.MIGRATION_ROLE_PASSWORD,
    )  # must not raise


@pytest.fixture()
def bootstrapped_disposable_database():
    name = h.disposable_database_name("rolecollision")
    h.create_disposable_database(name)
    bootstrap_roles(h.role_bootstrap_config(name))
    try:
        yield name
    finally:
        h.drop_disposable_database(name)


def _role_snapshot(admin_engine, role_names) -> dict:
    with admin_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolcanlogin "
                "FROM pg_catalog.pg_roles WHERE rolname = ANY(:names)"
            ),
            {"names": list(role_names)},
        ).fetchall()
    return {row.rolname: tuple(row) for row in rows}


def test_attempted_colliding_rebootstrap_changes_nothing_and_ddl_remains_denied(
    bootstrapped_disposable_database,
):
    """Proof, against a real already-bootstrapped database: attempting to
    construct a colliding RoleBootstrapConfig (here: runtime == migration)
    never even reaches bootstrap_roles()'s first SQL statement, so the
    already-established role attributes and the runtime role's DDL-denied
    posture are exactly as they were before the attempt."""
    name = bootstrapped_disposable_database
    url = h.admin_url()
    admin_engine = create_engine(url.set(database="postgres"))
    try:
        before = _role_snapshot(admin_engine, [h.MIGRATION_ROLE, h.APP_ROLE])
        assert set(before) == {h.MIGRATION_ROLE, h.APP_ROLE}

        with pytest.raises(RoleCollisionError):
            RoleBootstrapConfig(
                host=url.host or "localhost",
                port=url.port or 5432,
                database=name,
                admin_user=url.username or "",
                admin_password=url.password or "",
                migration_role=h.MIGRATION_ROLE,
                migration_password=h.MIGRATION_ROLE_PASSWORD,
                app_role=h.MIGRATION_ROLE,  # collision: runtime == migration
                app_password="whatever",
            )

        after = _role_snapshot(admin_engine, [h.MIGRATION_ROLE, h.APP_ROLE])
        assert after == before
    finally:
        admin_engine.dispose()

    # Runtime DDL posture (established by the fixture's real bootstrap_roles
    # call) is untouched by the failed attempt above.
    app_engine = create_engine(h.app_database_url(name))
    try:
        with app_engine.connect() as conn:
            with pytest.raises(ProgrammingError, match="permission denied"):
                conn.execute(text("CREATE TABLE role_collision_should_not_exist (id INT)"))
            conn.rollback()
    finally:
        app_engine.dispose()
