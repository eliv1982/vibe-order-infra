"""Integration tests for GET /api/analytics/applications/{application_id}
against a real PostgreSQL test database.

Requires TEST_DATABASE_URL, exactly like test_api.py; the whole module is
skipped with an explicit reason if it's unset.
"""

import pytest

from tests.db_safety_guard import get_test_database_url
from tests.test_api import _application_payload

TEST_DATABASE_URL = get_test_database_url()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set - skipping PostgreSQL integration tests",
)


def _create_application(client, **overrides) -> int:
    return client.post("/api/applications", json=_application_payload(**overrides)).json()["id"]


def _create_application_with_capability(client, **overrides) -> tuple[int, str]:
    body = client.post("/api/applications", json=_application_payload(**overrides)).json()
    return body["id"], body["behavior_metrics_capability"]


# --- auth and existence -------------------------------------------------


def test_detail_requires_token(client):
    response = client.get("/api/analytics/applications/999999")
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


def test_detail_rejects_corrupted_token(client):
    response = client.get(
        "/api/analytics/applications/999999",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401


def test_detail_missing_application_returns_404(client, admin_auth_headers):
    response = client.get("/api/analytics/applications/999999", headers=admin_auth_headers)
    assert response.status_code == 404


# --- has_metrics = false -------------------------------------------------


def test_detail_application_without_metrics_returns_empty_shape(client, admin_auth_headers):
    application_id = _create_application(client)

    response = client.get(f"/api/analytics/applications/{application_id}", headers=admin_auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["application_id"] == application_id
    assert body["has_metrics"] is False
    assert body["time_on_page_seconds"] is None
    assert body["return_count"] is None
    assert body["clicked_buttons"] == []
    assert body["section_activity"] == []
    assert body["total_button_clicks"] == 0
    assert body["recorded_at"] is None


# --- one metric ----------------------------------------------------------


def test_detail_single_metric(client, admin_auth_headers):
    application_id, capability = _create_application_with_capability(client)
    client.post(
        "/api/behavior-metrics",
        json={
            "application_id": application_id,
            "capability": capability,
            "time_on_page": 42,
            "return_count": 3,
            "clicked_buttons": [{"button": "cta", "count": 2}],
            "cursor_hover_data": {"hero": {"hovers": 1, "ms": 1500}},
        },
    )

    response = client.get(f"/api/analytics/applications/{application_id}", headers=admin_auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["has_metrics"] is True
    assert body["time_on_page_seconds"] == 42.0
    assert body["return_count"] == 3
    assert body["total_button_clicks"] == 2
    assert body["clicked_buttons"][0]["name"] == "cta"
    assert body["clicked_buttons"][0]["count"] == 2
    assert body["section_activity"][0]["section"] == "hero"
    assert body["section_activity"][0]["total_duration_seconds"] == 1.5
    assert body["recorded_at"] is not None


def test_second_metric_for_same_application_is_rejected_by_unique_constraint(client, admin_auth_headers):
    """BehaviorMetric.application_id is UNIQUE (app/models/behavior_metric.py),
    so at most one metric row can ever exist per application. This is why
    "multiple metrics per application" for the detail endpoint's
    aggregation is unit-tested against the pure service directly
    (test_behavior_analytics_service.py::test_detail_multiple_metrics_are_summed_deterministically)
    rather than exercised here: the DB schema makes it unreachable through
    the public API, and this test locks in exactly why.

    The second attempt gets 403, not 409: its capability was already
    consumed by the first submission, so it fails the same single neutral
    invalid-capability check every other invalid-capability case fails (see
    routes/behavior_metrics.py and tests/test_behavior_metrics_capability.py)
    - "this application already has metrics" no longer gets its own,
    distinguishable response."""
    application_id, capability = _create_application_with_capability(client)
    first = client.post(
        "/api/behavior-metrics",
        json={"application_id": application_id, "capability": capability},
    )
    assert first.status_code == 201

    second = client.post(
        "/api/behavior-metrics",
        json={"application_id": application_id, "capability": capability},
    )
    assert second.status_code == 403


# --- malformed JSON --------------------------------------------------------


def test_detail_malformed_json_does_not_500(client, admin_auth_headers):
    application_id, capability = _create_application_with_capability(client)
    create_resp = client.post(
        "/api/behavior-metrics",
        json={
            "application_id": application_id,
            "capability": capability,
            "clicked_buttons": [{"button": "x"}, 123, None, {"count": 5}],
            "cursor_hover_data": {"hero": "not-a-dict", "": {"hovers": 1, "ms": 1}},
        },
    )
    assert create_resp.status_code == 201

    response = client.get(f"/api/analytics/applications/{application_id}", headers=admin_auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["clicked_buttons"] == []
    assert body["section_activity"] == []


# --- privacy -----------------------------------------------------------


def test_detail_does_not_leak_contact_data_or_full_application(client, admin_auth_headers):
    application_id = _create_application(
        client, contact_data="secret@example.com", first_name="SecretName"
    )

    response = client.get(f"/api/analytics/applications/{application_id}", headers=admin_auth_headers)
    body_text = response.text
    assert "secret@example.com" not in body_text
    assert "SecretName" not in body_text
    assert "contact_data" not in body_text
    assert "business_info" not in body_text
    assert "need_scope" not in body_text


# --- existing behavior-metrics contract is unaffected -----------------------


def test_behavior_metrics_crud_contract_is_unchanged(client, admin_auth_headers):
    """Guard against accidentally changing the pre-existing behavior-metrics
    CRUD contract while adding the new analytics routes."""
    application_id, capability = _create_application_with_capability(client)
    create_resp = client.post(
        "/api/behavior-metrics",
        json={"application_id": application_id, "capability": capability, "time_on_page": 5},
    )
    assert create_resp.status_code == 201
    metric_id = create_resp.json()["id"]

    get_resp = client.get(f"/api/behavior-metrics/{metric_id}", headers=admin_auth_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["time_on_page"] == 5
