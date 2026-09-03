"""Integration tests for the operator CLI bootstrap command (app/cli.py)
against a real PostgreSQL test database.

Requires TEST_DATABASE_URL, exactly like test_api.py; the whole module is
skipped with an explicit reason if it's unset. bootstrap_admin() accepts an
injectable session_factory precisely so it can be exercised here against
the disposable test database instead of the real SessionLocal/production
engine (see app/cli.py's docstring) - the real `python -m app.cli
bootstrap-admin` entry point always uses the defaults untouched.

bootstrap_admin() opens and closes its own session per call, exactly like
it would against the real SessionLocal - so these tests use fresh
Session(db_engine) instances (mirroring
test_auth_api.py::test_register_first_admin_race_condition_only_one_wins)
rather than the per-test savepoint-based `db_session` fixture, which a call
to bootstrap_admin() would prematurely close. Each test commits for real
and cleans up via the cleanup_admins fixture below.
"""

import threading

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app import cli
from app.crud import admin as admin_crud
from app.models.admin import Admin
from tests.db_safety_guard import get_test_database_url

TEST_DATABASE_URL = get_test_database_url()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set - skipping PostgreSQL integration tests",
)


def _credentials(username: str, password: str):
    return (lambda: username), (lambda: password)


@pytest.fixture()
def cleanup_admins(db_engine):
    yield
    with Session(db_engine) as session:
        session.execute(delete(Admin))
        session.commit()


def test_bootstrap_admin_creates_exactly_one_valid_admin(db_engine, cleanup_admins):
    read_username, read_password = _credentials("bootstrap-admin", "StrongPassw0rd!123")

    exit_code = cli.bootstrap_admin(
        session_factory=lambda: Session(db_engine),
        read_username=read_username,
        read_password=read_password,
    )

    assert exit_code == 0
    with Session(db_engine) as verify:
        assert admin_crud.count_admins(verify) == 1
        admin = admin_crud.get_admin_by_username(verify, "bootstrap-admin")
        assert admin is not None
        assert admin.is_active is True


def test_bootstrap_admin_rejects_a_second_admin(db_engine, cleanup_admins):
    with Session(db_engine) as setup:
        admin_crud.register_first_admin(setup, "existing-admin", "StrongPassw0rd!123")

    read_username, read_password = _credentials("attacker", "StrongPassw0rd!123")
    exit_code = cli.bootstrap_admin(
        session_factory=lambda: Session(db_engine),
        read_username=read_username,
        read_password=read_password,
    )

    assert exit_code != 0
    with Session(db_engine) as verify:
        assert admin_crud.count_admins(verify) == 1
        assert admin_crud.get_admin_by_username(verify, "attacker") is None


def test_bootstrap_admin_rejects_invalid_credentials_without_touching_the_db(
    db_engine, cleanup_admins
):
    # Both fail AdminRegister validation (username too short, password too
    # short) before session_factory is ever called.
    read_username, read_password = _credentials("ab", "short")

    exit_code = cli.bootstrap_admin(
        session_factory=lambda: Session(db_engine),
        read_username=read_username,
        read_password=read_password,
    )

    assert exit_code != 0
    with Session(db_engine) as verify:
        assert admin_crud.count_admins(verify) == 0


def test_bootstrap_admin_never_prints_the_password_or_a_hash(db_engine, cleanup_admins, capsys):
    password = "VeryS3cretPassphrase!"
    read_username, read_password = _credentials("printed-admin", password)

    exit_code = cli.bootstrap_admin(
        session_factory=lambda: Session(db_engine),
        read_username=read_username,
        read_password=read_password,
    )
    assert exit_code == 0

    with Session(db_engine) as verify:
        password_hash = admin_crud.get_admin_by_username(verify, "printed-admin").password_hash

    captured = capsys.readouterr()
    assert password not in captured.out
    assert password not in captured.err
    assert password_hash not in captured.out
    assert password_hash not in captured.err


class _ExplodingSession:
    """Wraps a real Session; every attribute/method delegates to it except
    commit(), which raises an unexpected (non-IntegrityError) exception -
    simulating a DB/connection failure that admin_crud.register_first_admin
    does not itself catch, so it propagates to bootstrap_admin()'s
    top-level exception handling. The raised exception's message embeds a
    marker standing in for "what a real SQLAlchemy StatementError/DBAPIError
    repr would include" (bound INSERT parameters) - see
    test_bootstrap_admin_unexpected_db_failure_never_leaks_the_generated_hash
    below, which makes that marker equal to the *actual* value
    register_first_admin would have hashed the password to, by monkeypatching
    hash_password to be deterministic for the duration of that test.
    """

    def __init__(self, real_session: Session, failure_message: str) -> None:
        self._real = real_session
        self._failure_message = failure_message

    def __getattr__(self, name):
        return getattr(self._real, name)

    def commit(self) -> None:
        raise RuntimeError(self._failure_message)


def test_bootstrap_admin_unexpected_db_failure_never_leaks_the_generated_hash(
    db_engine, cleanup_admins, capsys, monkeypatch
):
    """Regression test for the CLI credential-leak finding: an independent
    audit reproduced that an unexpected DB failure during the admin INSERT
    (anything register_first_admin's own `except IntegrityError` doesn't
    catch) could let SQLAlchemy's exception rendering - which can include
    the failed statement's bound parameters - carry the freshly generated
    Argon2 password hash into the operator's terminal via an unhandled
    traceback. The plaintext password was never exposed; the hash was.

    hash_password is monkeypatched to a fixed, recognizable value so the
    injected failure's message can embed the *exact* value
    register_first_admin would really have used as password_hash - not just
    an unrelated marker - closing the loop between "what was generated" and
    "what must never leak".
    """
    generated_hash_marker = (
        "$argon2id$v=19$m=19456,t=2,p=1$"
        "REGRESSION_TEST_DETERMINISTIC_SENSITIVE_HASH_MARKER_Zm9vYmFyYmF6"
    )
    monkeypatch.setattr(admin_crud, "hash_password", lambda password: generated_hash_marker)

    password = "YetAnotherSecretPassphrase!7"
    read_username, read_password = _credentials("exploding-admin", password)

    failure_message = (
        "(psycopg.errors.SomeUnexpectedError) simulated failure executing "
        "INSERT INTO admins (username, password_hash) VALUES "
        f"('exploding-admin', '{generated_hash_marker}')"
    )

    def _exploding_session_factory() -> Session:
        return _ExplodingSession(Session(db_engine), failure_message)  # type: ignore[return-value]

    exit_code = cli.bootstrap_admin(
        session_factory=_exploding_session_factory,
        read_username=read_username,
        read_password=read_password,
    )

    assert exit_code != 0

    captured = capsys.readouterr()
    assert password not in captured.out
    assert password not in captured.err
    assert generated_hash_marker not in captured.out
    assert generated_hash_marker not in captured.err
    # Nothing about the underlying exception (message, type name, or the
    # simulated driver error code) leaked either - the operator sees only
    # the fixed generic message.
    assert "psycopg" not in captured.out
    assert "psycopg" not in captured.err
    assert "RuntimeError" not in captured.out
    assert "RuntimeError" not in captured.err
    assert "No administrator was created" in captured.err

    # No admin was incorrectly created/committed - the failed commit's
    # pending insert must have been rolled back, not left half-applied.
    with Session(db_engine) as verify:
        assert admin_crud.count_admins(verify) == 0
        assert admin_crud.get_admin_by_username(verify, "exploding-admin") is None

    # Normal bootstrap must still succeed afterward - the failure above
    # must not have left the DB (or this session_factory's target) in a
    # state that blocks a legitimate subsequent attempt.
    read_username2, read_password2 = _credentials("real-admin-after-failure", "StrongPassw0rd!123")
    retry_exit_code = cli.bootstrap_admin(
        session_factory=lambda: Session(db_engine),
        read_username=read_username2,
        read_password=read_password2,
    )
    assert retry_exit_code == 0
    with Session(db_engine) as verify:
        assert admin_crud.count_admins(verify) == 1
        assert admin_crud.get_admin_by_username(verify, "real-admin-after-failure") is not None


def test_bootstrap_admin_concurrent_attempts_result_in_exactly_one_admin(db_engine, cleanup_admins):
    """Two truly concurrent `bootstrap-admin` invocations - e.g. an operator
    running the command twice by mistake, or from two terminals - must
    still leave exactly one admin. Mirrors
    test_auth_api.py::test_register_first_admin_race_condition_only_one_wins
    (same underlying advisory-lock protection - see
    app.crud.admin.register_first_admin) but exercised through the CLI
    entry point itself rather than the crud function directly, to prove the
    CLI's own session-per-call wiring doesn't undermine that guarantee.
    """
    start_barrier = threading.Barrier(2, timeout=10)
    usernames = ("cli-racer-one", "cli-racer-two")
    exit_codes: dict[str, int] = {}
    errors: list[BaseException] = []

    def attempt(username: str) -> None:
        try:
            start_barrier.wait()
            read_username, read_password = _credentials(username, "StrongPassw0rd!123")
            exit_codes[username] = cli.bootstrap_admin(
                session_factory=lambda: Session(db_engine),
                read_username=read_username,
                read_password=read_password,
            )
        except BaseException as exc:  # noqa: BLE001 - surfaced via `errors`, not swallowed
            errors.append(exc)

    threads = [threading.Thread(target=attempt, args=(name,)) for name in usernames]

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert not any(t.is_alive() for t in threads), (
        "race-condition threads did not finish within the timeout"
    )
    assert not errors, f"unexpected exception(s) in race threads: {errors!r}"
    assert set(exit_codes) == set(usernames)
    assert sorted(exit_codes.values()) == [0, 1]

    with Session(db_engine) as verify:
        assert admin_crud.count_admins(verify) == 1
