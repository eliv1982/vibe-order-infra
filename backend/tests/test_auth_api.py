"""Integration tests for the admin auth endpoints against a real PostgreSQL test database.

These exercise the full stack (FastAPI + SQLAlchemy + PostgreSQL) and require
TEST_DATABASE_URL, exactly like test_api.py; the whole module is skipped with
an explicit reason if it's unset.
"""

import threading
import time

import jwt
import pytest
from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import ConflictError, DomainValidationError
from app.core.security import decode_access_token, hash_password
from app.crud import admin as admin_crud
from app.models.admin import Admin
from tests.db_safety_guard import get_test_database_url

TEST_DATABASE_URL = get_test_database_url()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set - skipping PostgreSQL integration tests",
)


def _bootstrap_admin(db_session, username="admin", password="StrongPassw0rd!") -> Admin:
    """Create an admin directly through the domain layer - mirrors exactly
    what the operator CLI (app/cli.py) does, since there is no public HTTP
    registration endpoint to call instead (see app/routes/auth.py)."""
    from app.core.security import normalize_username

    return admin_crud.register_first_admin(db_session, normalize_username(username), password)


def _login(client, username="admin", password="StrongPassw0rd!"):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def test_check_reports_no_admin_before_bootstrap(client):
    response = client.get("/api/auth/check")
    assert response.status_code == 200
    assert response.json() == {"admin_exists": False}


def test_check_response_never_advertises_a_registration_state(client):
    """/auth/check must not expose anything like "registration_allowed" -
    whether the first admin can be self-registered over public HTTP is no
    longer a concept this API has at all (see AuthCheckResponse)."""
    response = client.get("/api/auth/check")
    assert response.status_code == 200
    assert set(response.json().keys()) == {"admin_exists"}


def test_public_register_endpoint_does_not_exist(client):
    """The unauthenticated first-admin-creation defect is fixed by removing
    the HTTP path entirely, not by gating it - POST /auth/register must not
    resolve to any route, on an empty database or otherwise."""
    response = client.post(
        "/api/auth/register", json={"username": "attacker", "password": "StrongPassw0rd!"}
    )
    assert response.status_code == 404


def test_public_register_cannot_create_the_first_admin_on_an_empty_db(client, db_session):
    """End-to-end proof of the fix: an empty database stays unclaimable over
    public HTTP - POST /auth/register (whether or not it exists as a route)
    can never result in an admin row."""
    response = client.post(
        "/api/auth/register", json={"username": "attacker", "password": "StrongPassw0rd!"}
    )
    assert response.status_code != 201
    assert admin_crud.count_admins(db_session) == 0


def test_check_reports_admin_exists_after_bootstrap(client, db_session):
    _bootstrap_admin(db_session)
    response = client.get("/api/auth/check")
    assert response.status_code == 200
    assert response.json() == {"admin_exists": True}


def test_register_first_admin_rejects_empty_username_when_called_directly(db_session):
    """CRUD-level backstop for a caller that bypasses the AdminRegister schema.

    The schema always normalizes and enforces min_length before this
    function is ever reached via /api/auth/register, so this exercises
    register_first_admin() directly rather than through the endpoint.
    """
    with pytest.raises(DomainValidationError):
        admin_crud.register_first_admin(db_session, "", "StrongPassw0rd!")

    assert admin_crud.count_admins(db_session) == 0


def test_register_first_admin_rejects_whitespace_only_username_when_called_directly(db_session):
    with pytest.raises(DomainValidationError):
        admin_crud.register_first_admin(db_session, "   ", "StrongPassw0rd!")

    assert admin_crud.count_admins(db_session) == 0


def test_login_success_returns_token_matching_bootstrapped_admin(client, db_session):
    admin = _bootstrap_admin(db_session)

    login_resp = _login(client, username="Admin")  # case must not matter
    assert login_resp.status_code == 200
    body = login_resp.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0

    payload = decode_access_token(body["access_token"])
    assert payload["sub"] == str(admin.id)


def test_login_with_unknown_username_and_wrong_password_return_identical_401(client, db_session):
    _bootstrap_admin(db_session)

    unknown_user_resp = _login(client, username="nobody", password="whatever123")
    wrong_password_resp = _login(client, username="admin", password="definitely-wrong")

    assert unknown_user_resp.status_code == 401
    assert wrong_password_resp.status_code == 401
    assert unknown_user_resp.json() == wrong_password_resp.json()


def test_login_for_inactive_admin_returns_same_neutral_401(client, db_session):
    admin = _bootstrap_admin(db_session)
    admin.is_active = False
    db_session.commit()

    response = _login(client)
    assert response.status_code == 401
    assert response.json() == {"detail": "Incorrect username or password"}


def test_me_returns_current_admin_without_password_hash(client, db_session):
    _bootstrap_admin(db_session)
    token = _login(client).json()["access_token"]

    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "admin"
    assert "password_hash" not in body


def test_me_without_token_returns_401(client):
    response = client.get("/api/auth/me")
    assert response.status_code == 401


def test_me_with_malformed_token_returns_401(client):
    response = client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401


def test_me_with_expired_token_returns_401(client, db_session):
    _bootstrap_admin(db_session)
    settings = get_settings()
    now = int(time.time())
    expired_payload = {"sub": "1", "type": "access", "iat": now - 120, "exp": now - 60}
    expired_token = jwt.encode(
        expired_payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm
    )

    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {expired_token}"})
    assert response.status_code == 401


def test_me_for_deactivated_admin_returns_401(client, db_session):
    admin = _bootstrap_admin(db_session)
    admin_id = admin.id
    token = _login(client).json()["access_token"]

    admin = admin_crud.get_admin(db_session, admin_id)
    admin.is_active = False
    db_session.commit()

    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_register_first_admin_race_condition_only_one_wins(db_engine):
    """Two truly concurrent registration attempts must not both succeed.

    This bypasses the client/db_session fixtures on purpose: those share one
    connection/savepoint per test and can't model two independent concurrent
    transactions the way two real concurrent HTTP requests would. It opens
    two separate Session objects (separate PostgreSQL connections) against
    the session-scoped db_engine and runs them in two threads.

    A threading.Barrier forces both threads to actually begin
    register_first_admin() at the same instant, rather than relying on
    thread-scheduling luck after two sequential .start() calls - without it,
    thread 1 could plausibly finish (acquire lock, commit, release lock)
    before thread 2 even starts, which would still "pass" but wouldn't have
    exercised any real lock contention.
    """
    start_barrier = threading.Barrier(2, timeout=10)
    usernames = ("racer-one", "racer-two")
    outcomes: dict[str, str] = {}
    errors: list[BaseException] = []

    def attempt(username: str) -> None:
        try:
            start_barrier.wait()
            with Session(db_engine) as session:
                try:
                    admin_crud.register_first_admin(session, username, "StrongPassw0rd!")
                    outcomes[username] = "ok"
                except ConflictError:
                    outcomes[username] = "conflict"
        except BaseException as exc:  # noqa: BLE001 - surfaced via `errors`, not swallowed
            errors.append(exc)

    threads = [threading.Thread(target=attempt, args=(name,)) for name in usernames]

    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert not any(t.is_alive() for t in threads), (
            "race-condition threads did not finish within the timeout"
        )
        assert not errors, f"unexpected exception(s) in race threads: {errors!r}"
        assert set(outcomes) == set(usernames)
        assert sorted(outcomes.values()) == ["conflict", "ok"]

        winner = next(name for name in usernames if outcomes[name] == "ok")

        with Session(db_engine) as verify_session:
            assert admin_crud.count_admins(verify_session) == 1
            assert admin_crud.get_admin_by_username(verify_session, winner) is not None
    finally:
        # Unlike the tests above (which only ever release a SAVEPOINT and
        # roll back for real at teardown), this test commits for real - it
        # must clean up so it doesn't leak a committed admin row into other
        # tests sharing this session-scoped db_engine.
        with Session(db_engine) as cleanup_session:
            cleanup_session.execute(delete(Admin))
            cleanup_session.commit()


def test_register_first_admin_waits_for_lock_then_rechecks_and_conflicts(db_engine):
    """Deterministic (non-racy) proof of the "wait on the lock, then re-check" path.

    Rather than relying on two threads happening to contend for the lock at
    the same moment, this test controls the ordering directly: session A
    manually takes the *same* advisory lock key that register_first_admin()
    uses internally and holds it open (uncommitted) while it creates the
    winning admin row itself. Session B then calls the real, unmodified
    register_first_admin() on a separate connection - since Postgres's
    pg_advisory_xact_lock blocks the caller until the lock is free, B is
    guaranteed (by Postgres's own lock semantics, not by thread timing) to
    be stuck waiting until A commits. Once A commits and releases the lock,
    B must resume, re-check the admin count (now 1, not 0), and raise
    ConflictError instead of inserting - proving the recheck-after-wait path
    actually works, not just the "nobody was contending" path.

    No test-only hook is added to production code for this: the holder side
    is expressed with plain SQLAlchemy/text() calls mirroring exactly what
    register_first_admin() does internally, on a second, independent session.
    """
    lock_key = admin_crud._FIRST_ADMIN_LOCK_KEY

    waiter_started = threading.Event()
    waiter_outcome: dict[str, object] = {}

    def waiter() -> None:
        with Session(db_engine) as session:
            waiter_started.set()
            try:
                admin_crud.register_first_admin(session, "waiter-admin", "StrongPassw0rd!")
                waiter_outcome["result"] = "ok"
            except ConflictError:
                waiter_outcome["result"] = "conflict"
            except BaseException as exc:  # noqa: BLE001 - surfaced via waiter_outcome
                waiter_outcome["error"] = exc

    holder_session = Session(db_engine)
    waiter_thread = threading.Thread(target=waiter)

    try:
        holder_session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})

        waiter_thread.start()

        # waiter_started only confirms the thread entered register_first_admin();
        # a short, generous grace period lets it actually reach and block on
        # the advisory-lock request (already held by holder_session) before
        # the holder commits. If this window were somehow missed, the waiter
        # would simply acquire the lock immediately instead of after
        # blocking - it would still correctly see count == 1 and conflict,
        # so the test's assertions hold either way; this only maximizes the
        # chance of exercising the actual "blocked, then unblocked" path.
        assert waiter_started.wait(timeout=5), "waiter thread did not start in time"
        time.sleep(0.3)

        holder_session.add(
            Admin(username="holder-admin", password_hash=hash_password("StrongPassw0rd!"))
        )
        holder_session.commit()  # releases the advisory lock; waiter can now proceed

        waiter_thread.join(timeout=10)
        assert not waiter_thread.is_alive(), "waiter thread did not finish within the timeout"
        assert "error" not in waiter_outcome, f"unexpected exception in waiter: {waiter_outcome!r}"
        assert waiter_outcome.get("result") == "conflict"

        with Session(db_engine) as verify_session:
            assert admin_crud.count_admins(verify_session) == 1
            assert admin_crud.get_admin_by_username(verify_session, "holder-admin") is not None
            assert admin_crud.get_admin_by_username(verify_session, "waiter-admin") is None
    finally:
        holder_session.close()
        waiter_thread.join(timeout=5)  # in case an earlier assertion failed before this ran
        with Session(db_engine) as cleanup_session:
            cleanup_session.execute(delete(Admin))
            cleanup_session.commit()
