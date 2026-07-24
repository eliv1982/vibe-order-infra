"""Integration tests against a real PostgreSQL test database.

These exercise the full stack (FastAPI + SQLAlchemy + PostgreSQL) and
require TEST_DATABASE_URL — a database dedicated to testing. They must
never run against the production DATABASE_URL/postgres service used by
docker-compose; if TEST_DATABASE_URL is not set, the whole module is
skipped with an explicit reason instead of failing.
"""

from decimal import Decimal

import pytest

from tests.db_safety_guard import get_test_database_url

TEST_DATABASE_URL = get_test_database_url()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set - skipping PostgreSQL integration tests",
)


def _application_payload(**overrides) -> dict:
    payload = {
        "first_name": "Ivan",
        "last_name": "Petrov",
        "contact_data": "ivan@example.com",
        "business_niche": "Retail",
        "company_size": "10-50",
        "business_info": "Online shop",
        "task_scope": "Website redesign",
        "requester_role": "owner",
        "business_size": "small",
        "need_scope": "full redesign",
        "deadline": "1 month",
        "task_type": "development",
        "interested_product": "Website",
        "budget": "1000.00",
        "preferred_contact_method": "email",
        "preferred_contact_time": "morning",
    }
    payload.update(overrides)
    return payload


def test_application_crud_roundtrip(client, admin_auth_headers):
    # Creating an application is public (client form); everything else here
    # is protected and needs the admin token.
    create_resp = client.post("/api/applications", json=_application_payload())
    assert create_resp.status_code == 201
    application_id = create_resp.json()["id"]

    get_resp = client.get(f"/api/applications/{application_id}", headers=admin_auth_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["first_name"] == "Ivan"

    patch_resp = client.patch(
        f"/api/applications/{application_id}",
        json={"first_name": "Petr"},
        headers=admin_auth_headers,
    )
    assert patch_resp.status_code == 200
    updated = patch_resp.json()
    assert updated["first_name"] == "Petr"
    # A partial PATCH must not clobber fields it didn't mention.
    assert updated["last_name"] == "Petrov"

    delete_resp = client.delete(f"/api/applications/{application_id}", headers=admin_auth_headers)
    assert delete_resp.status_code == 204

    missing_resp = client.get(f"/api/applications/{application_id}", headers=admin_auth_headers)
    assert missing_resp.status_code == 404


def test_application_patch_rejects_explicit_null_for_not_null_field(client, admin_auth_headers):
    application_id = client.post("/api/applications", json=_application_payload()).json()["id"]
    response = client.patch(
        f"/api/applications/{application_id}",
        json={"first_name": None},
        headers=admin_auth_headers,
    )
    assert response.status_code == 422


def test_behavior_metric_duplicate_returns_409(client):
    application_id = client.post("/api/applications", json=_application_payload()).json()["id"]

    first = client.post(
        "/api/behavior-metrics", json={"application_id": application_id, "time_on_page": 30}
    )
    assert first.status_code == 201

    duplicate = client.post(
        "/api/behavior-metrics", json={"application_id": application_id, "time_on_page": 60}
    )
    assert duplicate.status_code == 409


def test_behavior_metric_for_missing_application_returns_404(client):
    response = client.post(
        "/api/behavior-metrics", json={"application_id": 999999, "time_on_page": 10}
    )
    assert response.status_code == 404


def test_deleting_application_cascades_to_behavior_metric(client, admin_auth_headers):
    application_id = client.post("/api/applications", json=_application_payload()).json()["id"]
    metric_id = client.post(
        "/api/behavior-metrics", json={"application_id": application_id, "time_on_page": 15}
    ).json()["id"]

    delete_resp = client.delete(f"/api/applications/{application_id}", headers=admin_auth_headers)
    assert delete_resp.status_code == 204

    missing_metric = client.get(f"/api/behavior-metrics/{metric_id}", headers=admin_auth_headers)
    assert missing_metric.status_code == 404


def test_admin_setting_active_endpoint_returns_only_active(client, admin_auth_headers):
    active = client.post(
        "/api/admin-settings",
        json={"service_name": "Active Service", "budget_min": 100, "budget_max": 500, "is_active": True},
        headers=admin_auth_headers,
    ).json()
    client.post(
        "/api/admin-settings",
        json={
            "service_name": "Inactive Service",
            "budget_min": 100,
            "budget_max": 500,
            "is_active": False,
        },
        headers=admin_auth_headers,
    )

    # No token here on purpose - /admin-settings/active must stay public.
    response = client.get("/api/admin-settings/active")
    assert response.status_code == 200
    items = response.json()
    assert active["id"] in [item["id"] for item in items]
    assert "Inactive Service" not in {item["service_name"] for item in items}


def test_admin_setting_patch_rejects_invalid_merged_budget_range_via_budget_min(
    client, admin_auth_headers
):
    setting = client.post(
        "/api/admin-settings",
        json={"service_name": "Consulting", "budget_min": 100, "budget_max": 500, "is_active": True},
        headers=admin_auth_headers,
    ).json()

    # Only budget_min is patched; the stored budget_max (500) makes the
    # resulting merged state 700 > 500, which must be rejected.
    response = client.patch(
        f"/api/admin-settings/{setting['id']}",
        json={"budget_min": 700},
        headers=admin_auth_headers,
    )
    assert response.status_code == 422


def test_admin_setting_patch_rejects_invalid_merged_budget_range_via_budget_max(
    client, admin_auth_headers
):
    setting = client.post(
        "/api/admin-settings",
        json={"service_name": "Consulting", "budget_min": 300, "budget_max": 500, "is_active": True},
        headers=admin_auth_headers,
    ).json()

    # Only budget_max is patched down; the stored budget_min (300) then
    # exceeds the new budget_max (100), which must be rejected.
    response = client.patch(
        f"/api/admin-settings/{setting['id']}",
        json={"budget_max": 100},
        headers=admin_auth_headers,
    )
    assert response.status_code == 422


def test_admin_setting_rejected_budget_patch_leaves_persisted_values_unchanged(
    client, admin_auth_headers
):
    setting = client.post(
        "/api/admin-settings",
        json={"service_name": "Consulting", "budget_min": 100, "budget_max": 500, "is_active": True},
        headers=admin_auth_headers,
    ).json()

    rejected = client.patch(
        f"/api/admin-settings/{setting['id']}",
        json={"budget_min": 700},
        headers=admin_auth_headers,
    )
    assert rejected.status_code == 422

    persisted = client.get(
        f"/api/admin-settings/{setting['id']}", headers=admin_auth_headers
    ).json()
    assert Decimal(str(persisted["budget_min"])) == Decimal("100")
    assert Decimal(str(persisted["budget_max"])) == Decimal("500")


@pytest.mark.parametrize(
    "endpoint", ["/api/applications", "/api/admin-settings", "/api/behavior-metrics"]
)
@pytest.mark.parametrize("params", [{"skip": -1}, {"limit": 0}, {"limit": 101}])
def test_pagination_rejects_invalid_params(client, admin_auth_headers, endpoint, params):
    # All three targets are now-protected GET "" list endpoints, so a valid
    # token is required to reach the pagination validation being tested here
    # (an unauthenticated request would 401 before ever checking params).
    response = client.get(endpoint, params=params, headers=admin_auth_headers)
    assert response.status_code == 422
