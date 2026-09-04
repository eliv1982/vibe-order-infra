"""Additional edge-case coverage for app.db_admin.adopt_legacy's fingerprint
verification and idempotency, beyond the full end-to-end proof in
test_migrations_legacy_upgrade.py. All read-only/no-mutation claims here are
checked directly: a failed verification must leave the database exactly as
it was found.

Requires TEST_DATABASE_URL with CREATEDB+CREATEROLE rights - see
tests/stage2_db_helpers.py's module docstring.
"""

import pytest
from sqlalchemy import create_engine, inspect, text

from app.db_admin.adopt_legacy import LegacySchemaVerificationError, adopt_legacy_database
from app.db_admin.bootstrap_roles import bootstrap_roles
from tests import stage2_db_helpers as h
from tests.test_migrations_legacy_upgrade import _LEGACY_SCHEMA_DDL

pytestmark = pytest.mark.skipif(
    not h.TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set - skipping PostgreSQL integration tests",
)


@pytest.fixture()
def bootstrapped_disposable_database():
    """A disposable database with the migration/app roles already
    established (adopt_legacy_database's ownership-reassignment step needs
    the migration role to exist), but no legacy schema seeded yet - each
    test builds exactly the schema shape it wants to verify against."""
    name = h.disposable_database_name("adopt")
    h.create_disposable_database(name)
    bootstrap_roles(h.role_bootstrap_config(name))
    try:
        yield name
    finally:
        h.drop_disposable_database(name)


def test_refuses_a_database_missing_a_legacy_table_entirely(bootstrapped_disposable_database):
    name = bootstrapped_disposable_database
    engine = create_engine(h.admin_url().set(database=name))
    try:
        with engine.begin() as conn:
            # Every legacy table except behavior_metrics - a database this
            # incomplete must never be silently accepted.
            conn.execute(
                text(
                    "CREATE TABLE admins (id SERIAL PRIMARY KEY, username VARCHAR(150) NOT NULL UNIQUE, "
                    "password_hash VARCHAR(255) NOT NULL, is_active BOOLEAN NOT NULL DEFAULT TRUE, "
                    "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"
                )
            )
    finally:
        engine.dispose()

    with pytest.raises(LegacySchemaVerificationError, match="admin_settings"):
        adopt_legacy_database(h.adoption_config(name))

    # Verification failure must not have created alembic_version or mutated anything.
    inspector = inspect(create_engine(h.admin_url().set(database=name)))
    assert "alembic_version" not in inspector.get_table_names()


def test_refuses_a_database_missing_an_expected_column(bootstrapped_disposable_database):
    name = bootstrapped_disposable_database
    engine = create_engine(h.admin_url().set(database=name))
    try:
        with engine.begin() as conn:
            conn.execute(text(_LEGACY_SCHEMA_DDL))
            # Simulate a legacy database with an unexpectedly narrower
            # admin_settings than expected (e.g. a manual hotfix removed a
            # column) - must be refused, not blindly stamped.
            conn.execute(text("ALTER TABLE admin_settings DROP COLUMN description"))
    finally:
        engine.dispose()

    with pytest.raises(LegacySchemaVerificationError, match="description"):
        adopt_legacy_database(h.adoption_config(name))


def test_is_idempotent_once_already_adopted(bootstrapped_disposable_database):
    name = bootstrapped_disposable_database
    engine = create_engine(h.admin_url().set(database=name))
    try:
        with engine.begin() as conn:
            conn.execute(text(_LEGACY_SCHEMA_DDL))
    finally:
        engine.dispose()

    first_message = adopt_legacy_database(h.adoption_config(name))
    assert "adopted" in first_message.lower()

    # Second call: alembic_version already exists - must short-circuit as a
    # no-op rather than re-running verification (which would now fail, since
    # the database has moved on - see this module's other tests - even
    # though it's genuinely already-adopted and nothing is wrong).
    second_message = adopt_legacy_database(h.adoption_config(name))
    assert "already adopted" in second_message.lower()


def test_verification_never_mutates_business_rows_on_failure(bootstrapped_disposable_database):
    name = bootstrapped_disposable_database
    engine = create_engine(h.admin_url().set(database=name))
    try:
        with engine.begin() as conn:
            conn.execute(text(_LEGACY_SCHEMA_DDL))
            conn.execute(
                text(
                    "INSERT INTO admins (username, password_hash, is_active) "
                    "VALUES ('untouched', 'hash', true)"
                )
            )
            # Force a verification failure (already-upgraded shape) without
            # actually running the real migration.
            conn.execute(text("ALTER TABLE applications ADD COLUMN service_id INTEGER NULL"))
    finally:
        engine.dispose()

    with pytest.raises(LegacySchemaVerificationError):
        adopt_legacy_database(h.adoption_config(name))

    verify_engine = create_engine(h.admin_url().set(database=name))
    try:
        with verify_engine.connect() as conn:
            username = conn.execute(text("SELECT username FROM admins")).scalar_one()
        assert username == "untouched"
    finally:
        verify_engine.dispose()


def test_accepts_the_exact_legacy_fingerprint(bootstrapped_disposable_database):
    """Positive control for the strengthened fingerprint: the real,
    unmodified legacy shape (the same fixture test_migrations_legacy_
    upgrade.py builds its end-to-end proof on) must still be accepted -
    tightening verification must never reject a genuinely legacy-shaped
    database."""
    name = bootstrapped_disposable_database
    engine = create_engine(h.admin_url().set(database=name))
    try:
        with engine.begin() as conn:
            conn.execute(text(_LEGACY_SCHEMA_DDL))
    finally:
        engine.dispose()

    message = adopt_legacy_database(h.adoption_config(name))
    assert "adopted" in message.lower()

    verify_engine = create_engine(h.admin_url().set(database=name))
    try:
        with verify_engine.connect() as conn:
            stamped = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert stamped == "0001_legacy_baseline"
    finally:
        verify_engine.dispose()


# ---------------------------------------------------------------------------
# MAJOR 1 correction: fail-closed fingerprint regression matrix.
#
# Each test below starts from the exact legacy DDL (with one seeded admins
# row, so "business rows unchanged on rejection" is actually checking
# something), introduces exactly one deliberate defect, and asserts three
# things: adoption is refused, no alembic_version table was created, and the
# seeded row is untouched. Ownership is checked too - a rejected
# verification must never have run ALTER TABLE ... OWNER TO.
# ---------------------------------------------------------------------------

_SEED_ADMIN_USERNAME = "fingerprint_seed_admin"


@pytest.fixture()
def legacy_seeded_database(bootstrapped_disposable_database):
    """Exact legacy DDL plus one seeded admins row, on a database whose
    roles are already bootstrapped (but not yet adopted) - the common
    starting point every defect test below mutates from."""
    name = bootstrapped_disposable_database
    engine = create_engine(h.admin_url().set(database=name))
    try:
        with engine.begin() as conn:
            conn.execute(text(_LEGACY_SCHEMA_DDL))
            conn.execute(
                text(
                    "INSERT INTO admins (username, password_hash, is_active) "
                    f"VALUES ('{_SEED_ADMIN_USERNAME}', 'hash', true)"
                )
            )
    finally:
        engine.dispose()
    return name


def _assert_rejected_without_any_mutation(name: str) -> None:
    """Shared assertion block for every defect test: adoption must have
    raised (the caller does that via pytest.raises), and afterwards none of
    - alembic_version existing, table ownership, or the seeded business row -
    may have changed."""
    engine = create_engine(h.admin_url().set(database=name))
    try:
        inspector = inspect(engine)
        assert "alembic_version" not in inspector.get_table_names()

        with engine.connect() as conn:
            owner = conn.execute(
                text("SELECT tableowner FROM pg_tables WHERE schemaname = 'public' AND tablename = 'admins'")
            ).scalar_one()
            # Tables were created by the admin connection itself (the DDL
            # above ran as h.admin_url()'s user) - a rejected adoption must
            # never have reassigned ownership to the migration role.
            assert owner != h.MIGRATION_ROLE

            username = conn.execute(
                text("SELECT username FROM admins WHERE username = :u"), {"u": _SEED_ADMIN_USERNAME}
            ).scalar_one()
            assert username == _SEED_ADMIN_USERNAME
    finally:
        engine.dispose()


def test_refuses_wrong_numeric_column_type(legacy_seeded_database):
    name = legacy_seeded_database
    engine = create_engine(h.admin_url().set(database=name))
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE applications ALTER COLUMN budget TYPE TEXT"))
    finally:
        engine.dispose()

    with pytest.raises(LegacySchemaVerificationError, match="budget"):
        adopt_legacy_database(h.adoption_config(name))
    _assert_rejected_without_any_mutation(name)


def test_refuses_wrong_numeric_precision_or_scale(legacy_seeded_database):
    name = legacy_seeded_database
    engine = create_engine(h.admin_url().set(database=name))
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE admin_settings ALTER COLUMN budget_min TYPE NUMERIC(10, 2)"))
    finally:
        engine.dispose()

    with pytest.raises(LegacySchemaVerificationError, match="budget_min"):
        adopt_legacy_database(h.adoption_config(name))
    _assert_rejected_without_any_mutation(name)


def test_refuses_wrong_varchar_length(legacy_seeded_database):
    name = legacy_seeded_database
    engine = create_engine(h.admin_url().set(database=name))
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE applications ALTER COLUMN first_name TYPE VARCHAR(50)"))
    finally:
        engine.dispose()

    with pytest.raises(LegacySchemaVerificationError, match="first_name"):
        adopt_legacy_database(h.adoption_config(name))
    _assert_rejected_without_any_mutation(name)


def test_refuses_wrong_nullability(legacy_seeded_database):
    name = legacy_seeded_database
    engine = create_engine(h.admin_url().set(database=name))
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE applications ALTER COLUMN contact_data DROP NOT NULL"))
    finally:
        engine.dispose()

    with pytest.raises(LegacySchemaVerificationError, match="contact_data"):
        adopt_legacy_database(h.adoption_config(name))
    _assert_rejected_without_any_mutation(name)


def test_refuses_a_missing_required_column(legacy_seeded_database):
    name = legacy_seeded_database
    engine = create_engine(h.admin_url().set(database=name))
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE admins DROP COLUMN password_hash"))
    finally:
        engine.dispose()

    with pytest.raises(LegacySchemaVerificationError, match="password_hash"):
        adopt_legacy_database(h.adoption_config(name))
    _assert_rejected_without_any_mutation(name)


def test_refuses_an_unexpected_extra_column(legacy_seeded_database):
    name = legacy_seeded_database
    engine = create_engine(h.admin_url().set(database=name))
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE admins ADD COLUMN loyalty_points INTEGER"))
    finally:
        engine.dispose()

    with pytest.raises(LegacySchemaVerificationError, match="loyalty_points"):
        adopt_legacy_database(h.adoption_config(name))
    _assert_rejected_without_any_mutation(name)


def test_refuses_a_missing_primary_key(legacy_seeded_database):
    name = legacy_seeded_database
    engine = create_engine(h.admin_url().set(database=name))
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE admin_settings DROP CONSTRAINT admin_settings_pkey"))
    finally:
        engine.dispose()

    with pytest.raises(LegacySchemaVerificationError, match="primary key"):
        adopt_legacy_database(h.adoption_config(name))
    _assert_rejected_without_any_mutation(name)


def test_refuses_a_missing_important_unique_constraint(legacy_seeded_database):
    name = legacy_seeded_database
    engine = create_engine(h.admin_url().set(database=name))
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE admins DROP CONSTRAINT admins_username_key"))
    finally:
        engine.dispose()

    with pytest.raises(LegacySchemaVerificationError, match="UNIQUE"):
        adopt_legacy_database(h.adoption_config(name))
    _assert_rejected_without_any_mutation(name)


def test_refuses_a_missing_foreign_key(legacy_seeded_database):
    name = legacy_seeded_database
    engine = create_engine(h.admin_url().set(database=name))
    try:
        with engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE behavior_metrics DROP CONSTRAINT behavior_metrics_application_id_fkey")
            )
    finally:
        engine.dispose()

    with pytest.raises(LegacySchemaVerificationError, match="FOREIGN KEY"):
        adopt_legacy_database(h.adoption_config(name))
    _assert_rejected_without_any_mutation(name)


def test_refuses_a_foreign_key_pointing_at_the_wrong_table(legacy_seeded_database):
    name = legacy_seeded_database
    engine = create_engine(h.admin_url().set(database=name))
    try:
        with engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE behavior_metrics DROP CONSTRAINT behavior_metrics_application_id_fkey")
            )
            conn.execute(
                text(
                    "ALTER TABLE behavior_metrics ADD CONSTRAINT behavior_metrics_application_id_fkey "
                    "FOREIGN KEY (application_id) REFERENCES admin_settings(id) ON DELETE CASCADE"
                )
            )
    finally:
        engine.dispose()

    with pytest.raises(LegacySchemaVerificationError, match="FOREIGN KEY"):
        adopt_legacy_database(h.adoption_config(name))
    _assert_rejected_without_any_mutation(name)


def test_refuses_a_foreign_key_pointing_at_a_same_named_table_in_another_schema(legacy_seeded_database):
    """MAJOR 2 correction: Codex's exact PoC - a `shadow` schema with a
    same-named `applications` table, and behavior_metrics.application_id
    redirected there. Table/column names alone made the old FK comparison
    consider this identical to the real public.applications - it must not:
    ref_table/ref_columns matching is meaningless if the schema differs."""
    name = legacy_seeded_database
    engine = create_engine(h.admin_url().set(database=name))
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE SCHEMA shadow"))
            conn.execute(text("CREATE TABLE shadow.applications (id INTEGER PRIMARY KEY)"))
            conn.execute(
                text("ALTER TABLE behavior_metrics DROP CONSTRAINT behavior_metrics_application_id_fkey")
            )
            conn.execute(
                text(
                    "ALTER TABLE behavior_metrics ADD CONSTRAINT behavior_metrics_application_id_fkey "
                    "FOREIGN KEY (application_id) REFERENCES shadow.applications(id) ON DELETE CASCADE"
                )
            )
    finally:
        engine.dispose()

    with pytest.raises(LegacySchemaVerificationError, match="FOREIGN KEY"):
        adopt_legacy_database(h.adoption_config(name))
    _assert_rejected_without_any_mutation(name)


def test_refuses_when_the_stage1a_capability_table_already_exists(legacy_seeded_database):
    name = legacy_seeded_database
    engine = create_engine(h.admin_url().set(database=name))
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE application_behavior_capabilities ("
                    "id SERIAL PRIMARY KEY, "
                    "application_id INTEGER NOT NULL UNIQUE REFERENCES applications(id) ON DELETE CASCADE, "
                    "capability_hash VARCHAR(64) NOT NULL UNIQUE, "
                    "used_at TIMESTAMPTZ, "
                    "created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
                )
            )
    finally:
        engine.dispose()

    with pytest.raises(LegacySchemaVerificationError, match="application_behavior_capabilities"):
        adopt_legacy_database(h.adoption_config(name))
    _assert_rejected_without_any_mutation(name)


def test_refuses_when_the_stage1b_service_id_column_already_exists(legacy_seeded_database):
    name = legacy_seeded_database
    engine = create_engine(h.admin_url().set(database=name))
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE applications ADD COLUMN service_id INTEGER NULL"))
    finally:
        engine.dispose()

    with pytest.raises(LegacySchemaVerificationError, match="service_id"):
        adopt_legacy_database(h.adoption_config(name))
    _assert_rejected_without_any_mutation(name)


def test_refuses_when_the_stage1b_idempotency_table_already_exists(legacy_seeded_database):
    name = legacy_seeded_database
    engine = create_engine(h.admin_url().set(database=name))
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE application_idempotency_keys ("
                    "id SERIAL PRIMARY KEY, "
                    "idempotency_key_hash VARCHAR(64) NOT NULL UNIQUE, "
                    "request_hash VARCHAR(64) NOT NULL, "
                    "application_id INTEGER REFERENCES applications(id) ON DELETE CASCADE, "
                    "created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
                )
            )
    finally:
        engine.dispose()

    with pytest.raises(LegacySchemaVerificationError, match="application_idempotency_keys"):
        adopt_legacy_database(h.adoption_config(name))
    _assert_rejected_without_any_mutation(name)


def test_refuses_a_representative_partial_or_manual_migration(legacy_seeded_database):
    """A database that doesn't match any *specific* known Stage 1A/1B shape,
    but has clearly been hand-modified beyond the legacy baseline (here: an
    unrelated manually-added table, the kind an operator hotfix might leave
    behind) - must still be refused generically, not only when the extra
    table happens to be one of the two named Stage 1A/1B additions."""
    name = legacy_seeded_database
    engine = create_engine(h.admin_url().set(database=name))
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE ops_audit_log (id SERIAL PRIMARY KEY, note TEXT)"))
    finally:
        engine.dispose()

    with pytest.raises(LegacySchemaVerificationError, match="ops_audit_log"):
        adopt_legacy_database(h.adoption_config(name))
    _assert_rejected_without_any_mutation(name)


# ---------------------------------------------------------------------------
# MAJOR 1 correction: legacy SERIAL/sequence semantics regression matrix.
#
# The old fingerprint only checked "does this column have *some* server-side
# default" - which a hand-set `DEFAULT 7` or a default pointing at a
# detached/wrongly-owned sequence both satisfy while being genuinely broken
# (the first duplicate id generated after adoption collides with an existing
# row). Each test below starts from the exact legacy DDL plus one seeded
# admins row (legacy_seeded_database), introduces exactly one deliberate
# sequence-semantics defect on admins.id, and asserts adoption is refused
# without mutating anything - including the admins_id_seq sequence's own
# last_value/is_called state, proven by snapshotting it immediately before
# and after the adoption attempt (verification must never call setval() to
# "fix" what it is checking).
# ---------------------------------------------------------------------------


def _sequence_state(name: str, sequence: str) -> tuple[int, bool]:
    engine = create_engine(h.admin_url().set(database=name))
    try:
        with engine.connect() as conn:
            row = conn.execute(text(f"SELECT last_value, is_called FROM {sequence}")).one()
        return (row.last_value, row.is_called)
    finally:
        engine.dispose()


def test_refuses_a_constant_default_instead_of_a_sequence(legacy_seeded_database):
    """Codex's exact PoC: DROP the SERIAL default and replace it with a
    constant. The old fingerprint accepted this (it only checked "has *a*
    default") - adoption then stamped/upgraded successfully, and the first
    generated id was the constant, with the second insert failing on a
    unique violation."""
    name = legacy_seeded_database
    before = _sequence_state(name, "admins_id_seq")
    engine = create_engine(h.admin_url().set(database=name))
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE admins ALTER COLUMN id DROP DEFAULT"))
            conn.execute(text("ALTER TABLE admins ALTER COLUMN id SET DEFAULT 7"))
    finally:
        engine.dispose()

    with pytest.raises(LegacySchemaVerificationError, match="not sequence-backed"):
        adopt_legacy_database(h.adoption_config(name))
    _assert_rejected_without_any_mutation(name)
    assert _sequence_state(name, "admins_id_seq") == before


def test_refuses_a_missing_id_default_entirely(legacy_seeded_database):
    name = legacy_seeded_database
    before = _sequence_state(name, "admins_id_seq")
    engine = create_engine(h.admin_url().set(database=name))
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE admins ALTER COLUMN id DROP DEFAULT"))
    finally:
        engine.dispose()

    with pytest.raises(LegacySchemaVerificationError, match="server-side default"):
        adopt_legacy_database(h.adoption_config(name))
    _assert_rejected_without_any_mutation(name)
    assert _sequence_state(name, "admins_id_seq") == before


def test_refuses_an_id_default_backed_by_a_detached_sequence(legacy_seeded_database):
    """The default is genuinely nextval()-backed and the referenced sequence
    genuinely exists - but PostgreSQL does not consider it OWNED BY
    admins.id (a freestanding sequence someone wired up by hand, e.g. after
    a manual schema patch), so pg_get_serial_sequence('admins', 'id') still
    resolves to the real admins_id_seq, not this one - a mismatch."""
    name = legacy_seeded_database
    before = _sequence_state(name, "admins_id_seq")
    engine = create_engine(h.admin_url().set(database=name))
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE SEQUENCE detached_admins_id_seq"))
            conn.execute(
                text("ALTER TABLE admins ALTER COLUMN id SET DEFAULT nextval('detached_admins_id_seq')")
            )
    finally:
        engine.dispose()

    with pytest.raises(LegacySchemaVerificationError, match="different object"):
        adopt_legacy_database(h.adoption_config(name))
    _assert_rejected_without_any_mutation(name)
    assert _sequence_state(name, "admins_id_seq") == before


def test_refuses_an_id_default_backed_by_a_sequence_owned_by_a_different_column(legacy_seeded_database):
    """The referenced sequence is real and *is* properly OWNED BY some
    column - just not this one: admin_settings_id_seq is legitimately bound
    to admin_settings.id, not admins.id. A sequence dependency that exists
    but points at the wrong table/column must be refused exactly like one
    that doesn't exist at all."""
    name = legacy_seeded_database
    before = _sequence_state(name, "admins_id_seq")
    engine = create_engine(h.admin_url().set(database=name))
    try:
        with engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE admins ALTER COLUMN id SET DEFAULT nextval('admin_settings_id_seq')")
            )
    finally:
        engine.dispose()

    with pytest.raises(LegacySchemaVerificationError, match="not owned/dependency-bound"):
        adopt_legacy_database(h.adoption_config(name))
    _assert_rejected_without_any_mutation(name)
    assert _sequence_state(name, "admins_id_seq") == before


def test_refuses_a_sequence_left_behind_the_tables_current_max_id(legacy_seeded_database):
    """A manually-inserted historical row with a high explicit id, combined
    with a sequence that was never advanced past it (e.g. restored from an
    incomplete backup, or edited by hand) - "has a nextval() default owned
    by the right column" is not enough; the next generated id must not
    collide with a row that already exists. Never silently setval()'d to
    "fix" this - refused instead, exactly like every other defect here."""
    name = legacy_seeded_database
    engine = create_engine(h.admin_url().set(database=name))
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO admins (id, username, password_hash, is_active) "
                    "VALUES (100, 'high_id_admin', 'hash', true)"
                )
            )
            conn.execute(text("SELECT setval('admins_id_seq', 5, true)"))
    finally:
        engine.dispose()

    before = _sequence_state(name, "admins_id_seq")
    assert before == (5, True)

    with pytest.raises(LegacySchemaVerificationError, match="would next generate"):
        adopt_legacy_database(h.adoption_config(name))
    _assert_rejected_without_any_mutation(name)
    assert _sequence_state(name, "admins_id_seq") == before


def test_accepts_the_exact_legitimate_serial_sequence(legacy_seeded_database):
    """Positive control for the strengthened sequence check: an untouched,
    genuinely legacy-shaped SERIAL id (real e93238b semantics) must still be
    adopted cleanly - tightening this check must never reject a real legacy
    database, only a corrupted-looking one."""
    name = legacy_seeded_database
    message = adopt_legacy_database(h.adoption_config(name))
    assert "adopted" in message.lower()
