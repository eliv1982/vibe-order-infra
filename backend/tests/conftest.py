"""Shared pytest fixtures.

Integration tests need a real PostgreSQL instance — the models use JSONB,
which SQLite cannot faithfully emulate — so they connect only through
TEST_DATABASE_URL, a database dedicated to testing. They must never touch
the production DATABASE_URL/postgres service used by docker-compose. If
TEST_DATABASE_URL is not set, integration tests are skipped with an
explicit reason rather than failing or silently falling back elsewhere.

Before any DDL (create_all/drop_all) runs against TEST_DATABASE_URL, its
database name is validated by assert_safe_test_database_url() (see
db_safety_guard.py) so a misconfigured TEST_DATABASE_URL can never point at
a production-looking database.

POSTGRES_USER/PASSWORD/DB placeholders below only satisfy Settings()
validation at import time (app.core.database builds a lazy SQLAlchemy
engine from them at module load — no connection is opened until something
queries it). Tests never query that engine: the `client` fixture overrides
the get_db dependency to use a session bound to TEST_DATABASE_URL instead,
and TestClient is used without a `with` block so the app's lifespan (which
would call Base.metadata.create_all on the production engine) never runs.
"""

import os

# Captured before the setdefault() calls below, so the safety guard can
# still compare against a real production POSTGRES_DB if one happens to be
# set in the environment the tests run in.
_PRODUCTION_POSTGRES_DB = os.environ.get("POSTGRES_DB")

os.environ.setdefault("POSTGRES_USER", "unused_placeholder_user")
os.environ.setdefault("POSTGRES_PASSWORD", "unused_placeholder_password")
os.environ.setdefault("POSTGRES_DB", "unused_placeholder_db")
# Fixed test-only secret - long enough to pass Settings' min_length=32 and
# distinct from the .env.example placeholder, so it isn't rejected as an
# insecure placeholder value. Never used outside the test suite.
os.environ.setdefault(
    "JWT_SECRET_KEY", "test-only-secret-key-for-pytest-do-not-use-in-prod-1234567890"
)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from tests.db_safety_guard import assert_safe_test_database_url, get_test_database_url

TEST_DATABASE_URL = get_test_database_url()


@pytest.fixture(scope="session")
def db_engine():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not set - skipping PostgreSQL integration tests")

    assert_safe_test_database_url(TEST_DATABASE_URL, _PRODUCTION_POSTGRES_DB)

    # Imported explicitly (not via app.main) so Base.metadata is guaranteed
    # to already contain applications/behavior_metrics/admin_settings before
    # create_all runs, regardless of fixture execution order.
    from app import models  # noqa: F401
    from app.core.database import Base

    engine = create_engine(TEST_DATABASE_URL)
    Base.metadata.create_all(bind=engine)

    yield engine

    # Re-validated before drop_all: teardown must never run DDL without its
    # own check, even though create_all already checked the same URL above.
    assert_safe_test_database_url(TEST_DATABASE_URL, _PRODUCTION_POSTGRES_DB)
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture()
def db_session(db_engine):
    connection = db_engine.connect()
    outer_transaction = connection.begin()
    # join_transaction_mode="create_savepoint": crud functions call
    # db.commit() for real, but here that only releases/restarts a SAVEPOINT
    # instead of ending the outer transaction, so the final rollback() below
    # discards everything the test did, keeping tests isolated from one
    # another without recreating tables per test.
    session = Session(bind=connection, join_transaction_mode="create_savepoint")

    yield session

    session.close()
    outer_transaction.rollback()
    connection.close()


@pytest.fixture()
def client(db_session):
    from app.core.database import get_db
    from app.main import app

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def admin_auth_headers(client) -> dict[str, str]:
    """Bearer auth headers for a freshly registered, active admin.

    Each test's db_session is its own isolated, rolled-back-at-teardown
    transaction (see db_session above), so registering a fixed
    username/password here can never collide with or leak into any other
    test, regardless of run order.
    """
    username = "admin"
    password = "StrongPassw0rd!123"

    register_response = client.post(
        "/api/auth/register", json={"username": username, "password": password}
    )
    assert register_response.status_code == 201, (
        f"admin registration failed with status {register_response.status_code}: "
        f"{register_response.text}"
    )

    login_response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert login_response.status_code == 200, (
        f"admin login failed with status {login_response.status_code}: {login_response.text}"
    )

    body = login_response.json()
    token = body.get("access_token")
    assert token, "admin login response is missing a non-empty access_token"
    assert body.get("token_type") == "bearer", (
        f"expected login token_type 'bearer', got {body.get('token_type')!r}"
    )

    return {"Authorization": f"Bearer {token}"}
