"""Stage 2 final correction: regression coverage for the last MAJOR finding -
the application schema was not fixed explicitly to "public", so reflection
and ORM/runtime behavior could follow PostgreSQL's default "$user", public
search_path and silently validate/target a schema sharing the connecting
role's own name instead of the real public schema.

Three angles, matching the correction's own scope:

- Legacy adoption (app.db_admin.adopt_legacy) must verify/mutate public
  specifically, never a same-named schema the connecting admin role's own
  "$user" search_path token happens to resolve first.
- The real FastAPI lifespan (app.core.schema_check.ensure_database_ready)
  must refuse startup under the same shadowing - both via its table/column
  reflection (explicit schema=APPLICATION_SCHEMA now, never
  inspector.default_schema_name - see app.core.schema_introspect) and its
  independent current_schema() runtime guard.
- app.core.database's engine pins search_path=public at the connection
  level (see that module's own comment for the GUC-precedence rationale) -
  proven here to survive even an unsafe role-level ALTER ROLE ... SET
  search_path override, not just PostgreSQL's own "$user" default.

Requires TEST_DATABASE_URL with CREATEDB+CREATEROLE rights - see
tests/stage2_db_helpers.py's module docstring.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

import app.main as main_module
from app.core.schema_check import DatabaseNotMigratedError, ensure_database_ready
from app.core.schema_introspect import APPLICATION_SCHEMA
from app.db_admin.adopt_legacy import LegacySchemaVerificationError, adopt_legacy_database
from app.db_admin.bootstrap_roles import bootstrap_roles
from tests import stage2_db_helpers as h
from tests.test_migrations_legacy_upgrade import _LEGACY_SCHEMA_DDL

pytestmark = pytest.mark.skipif(
    not h.TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set - skipping PostgreSQL integration tests",
)


def _pinned_connect_args() -> dict:
    """The exact connect_args app.core.database's real engine uses - see
    that module's own comment for the GUC-precedence rationale."""
    return {"options": f"-c search_path={APPLICATION_SCHEMA}"}


def _create_role_named_shadow_schema(engine, role: str) -> None:
    """Creates a schema literally named after `role` and grants that role
    USAGE on it plus default privileges on anything later created inside it
    by the connection running this - reproducing the exact shape
    PostgreSQL's default "$user", public search_path needs to silently
    resolve an unqualified name to this schema instead of public, for
    connections authenticated as `role`."""
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{role}"'))
        conn.execute(text(f'GRANT USAGE ON SCHEMA "{role}" TO "{role}"'))
        conn.execute(text(f'GRANT ALL ON SCHEMA "{role}" TO "{role}"'))
        conn.execute(
            text(f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{role}" GRANT ALL ON TABLES TO "{role}"')
        )


# ---------------------------------------------------------------------------
# A. Legacy adoption must reject based on the real public schema, not a
# role-named shadow the connecting admin role's own search_path resolves.
# ---------------------------------------------------------------------------


@pytest.fixture()
def role_shadowed_legacy_database():
    """A disposable database where: public holds a legacy schema with one
    deliberate incompatibility (a wrong column type), and a schema named
    after the admin/bootstrap role (the identity adopt_legacy.py's
    verification connects as - see AdoptionConfig.admin_user) holds a fully
    valid-looking legacy copy. PostgreSQL's default search_path would make
    that role-named schema the connecting admin's effective default schema."""
    name = h.disposable_database_name("roleschema_adopt")
    h.create_disposable_database(name)
    admin_role = h.admin_url().username
    engine = create_engine(h.admin_url().set(database=name))
    try:
        with engine.begin() as conn:
            conn.execute(text(_LEGACY_SCHEMA_DDL))
            conn.execute(
                text(
                    "INSERT INTO admins (username, password_hash, is_active) "
                    "VALUES ('public_seed_admin', 'hash', true)"
                )
            )
            # The one deliberate incompatibility: public's applications.budget
            # no longer matches the expected legacy NUMERIC(12, 2).
            conn.execute(text("ALTER TABLE applications ALTER COLUMN budget TYPE TEXT"))

        _create_role_named_shadow_schema(engine, admin_role)
        with engine.begin() as conn:
            conn.execute(text(f'SET LOCAL search_path TO "{admin_role}"'))
            conn.execute(text(_LEGACY_SCHEMA_DDL))
            conn.execute(
                text(
                    "INSERT INTO admins (username, password_hash, is_active) "
                    "VALUES ('shadow_seed_admin', 'hash', true)"
                )
            )

        bootstrap_roles(h.role_bootstrap_config(name))
        yield name
    finally:
        engine.dispose()
        h.drop_disposable_database(name)


def test_legacy_adoption_rejects_based_on_the_real_public_schema(role_shadowed_legacy_database):
    name = role_shadowed_legacy_database
    admin_role = h.admin_url().username

    with pytest.raises(LegacySchemaVerificationError, match="budget"):
        adopt_legacy_database(h.adoption_config(name))

    verify_engine = create_engine(h.admin_url().set(database=name))
    try:
        inspector = inspect(verify_engine)
        assert "alembic_version" not in inspector.get_table_names(schema=APPLICATION_SCHEMA)

        with verify_engine.connect() as conn:
            owner = conn.execute(
                text(
                    "SELECT tableowner FROM pg_tables WHERE schemaname = 'public' "
                    "AND tablename = 'applications'"
                )
            ).scalar_one()
            assert owner != h.MIGRATION_ROLE

            public_username = conn.execute(
                text("SELECT username FROM public.admins WHERE username = :u"),
                {"u": "public_seed_admin"},
            ).scalar_one()
            assert public_username == "public_seed_admin"

            shadow_username = conn.execute(
                text(f'SELECT username FROM "{admin_role}".admins WHERE username = :u'),
                {"u": "shadow_seed_admin"},
            ).scalar_one()
            assert shadow_username == "shadow_seed_admin"
    finally:
        verify_engine.dispose()


# ---------------------------------------------------------------------------
# B/C. The real FastAPI lifespan must refuse startup under the same
# shadowing, and must still start normally when public is genuinely valid.
# ---------------------------------------------------------------------------


@pytest.fixture()
def head_schema_with_role_shadow():
    """A disposable, fully Stage-2-bootstrapped database where public holds
    the current (Alembic head) schema - genuinely valid, matching
    Base.metadata exactly - and a schema named after the runtime app role
    additionally holds a fully valid-looking current copy. Yields the
    database name; individual tests decide whether to introduce a defect
    into public afterward."""
    name = h.disposable_database_name("roleschema_head")
    h.create_disposable_database(name)
    config = h.role_bootstrap_config(name)
    bootstrap_roles(config)  # roles + default privileges, before any table exists

    from app import models  # noqa: F401
    from app.core.database import Base

    migration_engine = create_engine(h.migration_database_url(name))
    try:
        Base.metadata.create_all(bind=migration_engine)
    finally:
        migration_engine.dispose()

    bootstrap_roles(config)  # finalize: catches up the tables just created

    admin_engine = create_engine(h.admin_url().set(database=name))
    try:
        _create_role_named_shadow_schema(admin_engine, h.APP_ROLE)
        with admin_engine.begin() as conn:
            conn.execute(text(f'SET LOCAL search_path TO "{h.APP_ROLE}"'))
            Base.metadata.create_all(bind=conn)
        yield name
    finally:
        admin_engine.dispose()
        h.drop_disposable_database(name)


def test_real_lifespan_refuses_startup_when_public_is_broken_despite_a_valid_shadow(
    head_schema_with_role_shadow,
):
    """Negative control: public is deliberately broken (wrong budget column
    type) while the role-named shadow schema holds a fully valid-looking
    current copy. Connects as the runtime app role with no search_path
    protection (a bare engine, unlike app.core.database's real one) - the
    startup guard must still refuse, proving neither the reflection checks
    nor the current_schema() guard can be fooled by the shadow."""
    name = head_schema_with_role_shadow
    admin_engine = create_engine(h.admin_url().set(database=name))
    try:
        with admin_engine.begin() as conn:
            conn.execute(text("ALTER TABLE applications ALTER COLUMN budget TYPE TEXT"))
    finally:
        admin_engine.dispose()

    unprotected_app_engine = create_engine(h.app_database_url(name))
    original_engine = main_module.engine
    main_module.engine = unprotected_app_engine
    try:
        with pytest.raises(DatabaseNotMigratedError):
            with TestClient(main_module.app):
                pass
    finally:
        main_module.engine = original_engine
        unprotected_app_engine.dispose()


def test_real_lifespan_starts_normally_when_public_is_valid_despite_a_role_named_shadow(
    head_schema_with_role_shadow,
):
    """Positive control: public is genuinely valid and untouched. Connects
    the same way app.core.database's real engine does (search_path pinned
    via connect_args) - startup must succeed and serve a request normally,
    proving the correction introduces no false positive merely because a
    role-named schema happens to exist."""
    name = head_schema_with_role_shadow
    pinned_app_engine = create_engine(h.app_database_url(name), connect_args=_pinned_connect_args())
    original_engine = main_module.engine
    main_module.engine = pinned_app_engine
    try:
        with TestClient(main_module.app) as client:
            response = client.get("/api/health")
        assert response.status_code == 200
    finally:
        main_module.engine = original_engine
        pinned_app_engine.dispose()


# ---------------------------------------------------------------------------
# 9. current_schema()/search_path itself, isolated from any table-shape
# comparison - proves app.core.schema_check's independent runtime guard, and
# that app.core.database's connection-level search_path pin survives even an
# unsafe role-level ALTER ROLE ... SET search_path override.
# ---------------------------------------------------------------------------


@pytest.fixture()
def shadowed_role_database():
    """A disposable, Stage-2-bootstrapped (but not migrated - no tables
    anywhere) database where a schema named after the runtime app role
    exists and that role has USAGE on it, reproducing the search_path
    ambiguity with no table-shape comparison involved at all."""
    name = h.disposable_database_name("searchpath")
    h.create_disposable_database(name)
    bootstrap_roles(h.role_bootstrap_config(name))
    admin_engine = create_engine(h.admin_url().set(database=name))
    try:
        _create_role_named_shadow_schema(admin_engine, h.APP_ROLE)
        yield name
    finally:
        admin_engine.dispose()
        h.drop_disposable_database(name)


def test_current_schema_resolves_to_the_shadow_without_any_protection(shadowed_role_database):
    """Sanity check that the fixture genuinely reproduces the ambiguity:
    connecting as the app role, whose own name now shadows public, with no
    search_path override at all, resolves current_schema() to the shadow -
    proving the negative-control tests below are real, not vacuous."""
    engine = create_engine(h.app_database_url(shadowed_role_database))
    try:
        with engine.connect() as conn:
            current = conn.execute(text("SELECT current_schema()")).scalar()
        assert current == h.APP_ROLE
    finally:
        engine.dispose()


def test_current_schema_is_public_for_a_role_with_no_shadow(shadowed_role_database):
    """Positive control: the migration role (also bootstrapped on this same
    database) shares no schema name with anything, so PostgreSQL's ordinary
    "$user", public default resolves it to public - the guard must not
    false-positive on perfectly normal configuration."""
    engine = create_engine(h.migration_database_url(shadowed_role_database))
    try:
        with engine.connect() as conn:
            current = conn.execute(text("SELECT current_schema()")).scalar()
        assert current == APPLICATION_SCHEMA
    finally:
        engine.dispose()


def test_ensure_database_ready_refuses_startup_when_current_schema_is_the_shadow(
    shadowed_role_database,
):
    engine = create_engine(h.app_database_url(shadowed_role_database))
    try:
        with pytest.raises(DatabaseNotMigratedError, match="current_schema"):
            ensure_database_ready(engine)
    finally:
        engine.dispose()


def test_the_refusal_is_read_only_and_mutates_no_schema_state(shadowed_role_database):
    name = shadowed_role_database
    engine = create_engine(h.app_database_url(name))
    try:
        with pytest.raises(DatabaseNotMigratedError):
            ensure_database_ready(engine)
    finally:
        engine.dispose()

    admin_engine = create_engine(h.admin_url().set(database=name))
    try:
        with admin_engine.connect() as conn:
            schemas = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT nspname FROM pg_catalog.pg_namespace WHERE nspname = ANY(:names)"
                    ),
                    {"names": ["public", h.APP_ROLE]},
                )
            }
        assert schemas == {"public", h.APP_ROLE}

        inspector = inspect(admin_engine)
        assert inspector.get_table_names(schema="public") == []
        assert inspector.get_table_names(schema=h.APP_ROLE) == []
    finally:
        admin_engine.dispose()


def test_search_path_pin_survives_an_unsafe_role_level_override(shadowed_role_database):
    """app.core.database's real engine pins search_path=public via
    connect_args (see that module's own comment). This proves the pin isn't
    merely redundant with PostgreSQL's own "$user" default: it also
    overrides a conflicting ALTER ROLE ... SET search_path, exactly the kind
    of unsafe/unexpected default it exists to defend against."""
    name = shadowed_role_database
    admin_engine = create_engine(h.admin_url().set(database=name))
    try:
        with admin_engine.begin() as conn:
            conn.execute(text(f'ALTER ROLE "{h.APP_ROLE}" SET search_path = "{h.APP_ROLE}", public'))
    finally:
        admin_engine.dispose()

    try:
        unpinned_engine = create_engine(h.app_database_url(name))
        try:
            with unpinned_engine.connect() as conn:
                current = conn.execute(text("SELECT current_schema()")).scalar()
            # The ALTER ROLE override genuinely took effect - otherwise the
            # comparison below would prove nothing.
            assert current == h.APP_ROLE
        finally:
            unpinned_engine.dispose()

        pinned_engine = create_engine(h.app_database_url(name), connect_args=_pinned_connect_args())
        try:
            with pinned_engine.connect() as conn:
                current = conn.execute(text("SELECT current_schema()")).scalar()
            assert current == APPLICATION_SCHEMA
        finally:
            pinned_engine.dispose()
    finally:
        cleanup_engine = create_engine(h.admin_url().set(database=name))
        try:
            with cleanup_engine.begin() as conn:
                conn.execute(text(f'ALTER ROLE "{h.APP_ROLE}" RESET search_path'))
        finally:
            cleanup_engine.dispose()


def test_ensure_database_ready_passes_despite_an_unsafe_role_level_override(
    head_schema_with_role_shadow,
):
    """Full end-to-end version of the search_path-pin proof above, against
    the real startup guard rather than a bare current_schema() probe: with
    both an unsafe ALTER ROLE ... SET search_path override AND a role-named
    shadow schema in place, a connection built the same way app.core.
    database builds its real engine still passes ensure_database_ready -
    proving the pin defeats the override for actual schema validation, not
    merely for a raw current_schema() SELECT."""
    name = head_schema_with_role_shadow
    admin_engine = create_engine(h.admin_url().set(database=name))
    try:
        with admin_engine.begin() as conn:
            conn.execute(text(f'ALTER ROLE "{h.APP_ROLE}" SET search_path = "{h.APP_ROLE}", public'))

        pinned_engine = create_engine(h.app_database_url(name), connect_args=_pinned_connect_args())
        try:
            ensure_database_ready(pinned_engine)  # must not raise
        finally:
            pinned_engine.dispose()
    finally:
        with admin_engine.begin() as conn:
            conn.execute(text(f'ALTER ROLE "{h.APP_ROLE}" RESET search_path'))
        admin_engine.dispose()
