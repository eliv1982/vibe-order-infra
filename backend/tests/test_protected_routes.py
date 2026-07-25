"""Integration tests proving the admin-CRUD authorization policy end-to-end.

Requires TEST_DATABASE_URL, exactly like test_api.py; the whole module is
skipped with an explicit reason if it's unset. test_api.py already re-proves
that authenticated CRUD still works exactly as before (same success codes);
this file focuses on the *wiring*: which endpoints require a token, which
stay public, and that failure modes converge on 401 without ever leaking into
404/409/422 territory or vice versa.
"""

import pytest

from app.crud import admin as admin_crud
from tests.db_safety_guard import get_test_database_url
from tests.test_api import _application_payload

TEST_DATABASE_URL = get_test_database_url()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set - skipping PostgreSQL integration tests",
)

# (method, path, json body) for every endpoint that must require a valid
# admin token. Placeholder ids (999999) are safe here even though nothing
# with that id exists: get_current_admin is a sub-dependency resolved before
# the handler body runs, so an unauthenticated/invalid-token request always
# 401s before the handler ever gets a chance to look the id up and 404.
# PATCH bodies are `{}` (a structurally valid partial update - every field in
# these Update schemas is optional) so a 422 can never masquerade as the 401
# this is actually testing.
PROTECTED_ENDPOINTS = [
    ("GET", "/api/applications", None),
    ("GET", "/api/applications/999999", None),
    ("PATCH", "/api/applications/999999", {}),
    ("DELETE", "/api/applications/999999", None),
    ("POST", "/api/admin-settings", {"service_name": "X", "budget_min": 1, "budget_max": 2}),
    ("GET", "/api/admin-settings", None),
    ("GET", "/api/admin-settings/999999", None),
    ("PATCH", "/api/admin-settings/999999", {}),
    ("DELETE", "/api/admin-settings/999999", None),
    ("GET", "/api/behavior-metrics", None),
    ("GET", "/api/behavior-metrics/999999", None),
    ("PATCH", "/api/behavior-metrics/999999", {}),
    ("DELETE", "/api/behavior-metrics/999999", None),
    ("GET", "/api/analytics/overview", None),
    ("GET", "/api/analytics/applications/999999", None),
]
_ENDPOINT_IDS = [f"{method} {path}" for method, path, _ in PROTECTED_ENDPOINTS]


@pytest.mark.parametrize("method, path, body", PROTECTED_ENDPOINTS, ids=_ENDPOINT_IDS)
def test_protected_endpoint_requires_token(client, method, path, body):
    response = client.request(method, path, json=body)
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


@pytest.mark.parametrize("method, path, body", PROTECTED_ENDPOINTS, ids=_ENDPOINT_IDS)
def test_protected_endpoint_rejects_corrupted_token(client, method, path, body):
    response = client.request(
        method, path, json=body, headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert response.status_code == 401


def test_create_application_stays_public(client):
    response = client.post("/api/applications", json=_application_payload())
    assert response.status_code == 201


def test_create_behavior_metric_stays_public(client):
    application_id = client.post("/api/applications", json=_application_payload()).json()["id"]
    response = client.post(
        "/api/behavior-metrics", json={"application_id": application_id, "time_on_page": 5}
    )
    assert response.status_code == 201


def test_admin_settings_active_endpoint_stays_public(client):
    response = client.get("/api/admin-settings/active")
    assert response.status_code == 200


def test_inactive_admin_is_rejected_by_protected_crud(client, admin_auth_headers, db_session):
    """get_current_admin's inactive-admin check is already fully unit-tested
    against /me in test_auth_api.py; this confirms the same check also fires
    when reached through a CRUD route's dependencies=[...] wiring, not just
    through a function-parameter Depends()."""
    admin = admin_crud.get_admin_by_username(db_session, "admin")
    admin.is_active = False
    db_session.commit()

    response = client.get("/api/applications", headers=admin_auth_headers)
    assert response.status_code == 401


def test_password_hash_never_appears_in_protected_responses(client, admin_auth_headers):
    application_id = client.post("/api/applications", json=_application_payload()).json()["id"]

    responses = [
        client.get("/api/applications", headers=admin_auth_headers),
        client.get(f"/api/applications/{application_id}", headers=admin_auth_headers),
        client.get("/api/admin-settings", headers=admin_auth_headers),
    ]
    for response in responses:
        assert "password_hash" not in response.text
