"""Stage 3: liveness (/api/health) vs readiness (/api/ready) contract.

See app/main.py's health_check/readiness_check docstrings for the intended
semantics this enforces: liveness never touches the database and must stay
healthy even when the database is down; readiness reuses
app.core.schema_check.ensure_database_ready - the same read-only guard
app.main's lifespan already runs once at boot - and fails closed (a non-2xx,
information-free body) whenever the database is unreachable or not on the
expected, fully migrated schema. test_schema_check.py already covers
ensure_database_ready's own logic in detail; these tests only check that the
two HTTP endpoints wire it up correctly (status codes, response bodies, and
that failures never leak connection/exception details).
"""

import app.main as main_module
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from tests import stage2_db_helpers as h

# A real TCP endpoint nothing listens on (port 0 is invalid to connect to,
# so port 1 with a short connect_timeout stands in for "database
# unreachable" without depending on any real PostgreSQL instance, and
# therefore without needing TEST_DATABASE_URL at all).
_UNREACHABLE_ENGINE_URL = "postgresql+psycopg://nobody:nopass@127.0.0.1:1/doesnotexist"


def _unreachable_engine():
    return create_engine(_UNREACHABLE_ENGINE_URL, connect_args={"connect_timeout": 2})


def test_liveness_is_ok_without_any_database_access():
    # TestClient is used without a `with` block (see tests/conftest.py's
    # module docstring) so app.main's lifespan - which *would* touch the
    # database - never runs here either; this proves /api/health itself does
    # no DB work, not merely that lifespan happened to be skipped.
    client = TestClient(main_module.app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_is_unhealthy_when_database_is_unreachable(monkeypatch):
    engine = _unreachable_engine()
    monkeypatch.setattr(main_module, "engine", engine)
    try:
        client = TestClient(main_module.app)
        response = client.get("/api/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "not_ready"}
    finally:
        engine.dispose()


def test_readiness_failure_body_has_no_sensitive_details(monkeypatch):
    engine = _unreachable_engine()
    monkeypatch.setattr(main_module, "engine", engine)
    try:
        client = TestClient(main_module.app)
        response = client.get("/api/ready")
        body_text = response.text.lower()
        for forbidden in ("nopass", "password", "traceback", "psycopg", "sqlalchemy", "127.0.0.1"):
            assert forbidden not in body_text
    finally:
        engine.dispose()


@pytest.mark.skipif(
    not h.TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set - skipping PostgreSQL integration tests"
)
def test_readiness_is_ready_on_a_fully_migrated_database(db_engine, monkeypatch):
    monkeypatch.setattr(main_module, "engine", db_engine)
    client = TestClient(main_module.app)
    response = client.get("/api/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


@pytest.mark.skipif(
    not h.TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set - skipping PostgreSQL integration tests"
)
def test_readiness_is_unhealthy_on_an_empty_unmigrated_database(monkeypatch):
    name = h.disposable_database_name("readiness")
    h.create_disposable_database(name)
    engine = create_engine(h.admin_url().set(database=name))
    try:
        monkeypatch.setattr(main_module, "engine", engine)
        client = TestClient(main_module.app)
        response = client.get("/api/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "not_ready"}
    finally:
        engine.dispose()
        h.drop_disposable_database(name)
