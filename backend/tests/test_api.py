"""Integration tests against a real PostgreSQL test database.

These exercise the full stack (FastAPI + SQLAlchemy + PostgreSQL) and
require TEST_DATABASE_URL — a database dedicated to testing. They must
never run against the production DATABASE_URL/postgres service used by
docker-compose; if TEST_DATABASE_URL is not set, the whole module is
skipped with an explicit reason instead of failing.
"""

from decimal import Decimal

import pytest

from tests.conftest import DEFAULT_TEST_SERVICE_ID
from tests.db_safety_guard import get_test_database_url

TEST_DATABASE_URL = get_test_database_url()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set - skipping PostgreSQL integration tests",
)


def _application_payload(**overrides) -> dict:
    # Every field below must be a value the backend actually accepts as of
    # Stage 1B (see app/schemas/application.py): business_niche/company_size/
    # business_size/requester_role/task_scope/task_type/deadline/
    # preferred_contact_method/preferred_contact_time are now closed Literal
    # enums (app/schemas/application_options.py, mirroring
    # frontend/src/options.ts exactly) rather than arbitrary strings, and
    # service_id (see DEFAULT_TEST_SERVICE_ID) replaces the old free-text
    # interested_product - the backend now derives interested_product itself
    # from the looked-up service.
    payload = {
        "first_name": "Ivan",
        "last_name": "Petrov",
        "contact_data": "ivan@example.com",
        "business_niche": "Личный автомобиль",
        "company_size": "Седан или универсал",
        "business_info": "Online shop",
        "task_scope": "Разовая услуга",
        "requester_role": "Владелец автомобиля",
        "business_size": "Один автомобиль",
        "need_scope": "full redesign",
        "deadline": "В течение месяца",
        "task_type": "Плановый уход",
        "service_id": DEFAULT_TEST_SERVICE_ID,
        "budget": "1000.00",
        "preferred_contact_method": "Телефон",
        "preferred_contact_time": "Утро (9:00–12:00)",
    }
    payload.update(overrides)
    return payload


def _create_application(client, **overrides) -> dict:
    """POST /applications and return the full response body, including the
    one-time behavior_metrics_capability - see
    app/schemas/application.py::ApplicationCreateRead."""
    response = client.post("/api/applications", json=_application_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


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


def test_behavior_metric_duplicate_returns_403_not_409(client):
    """A second submission for an application that already has metrics does
    NOT get a distinct 409 - the capability is already consumed by the
    first, successful submission, so it fails the same single neutral
    invalid-capability check (403) as any other invalid capability. See
    tests/test_behavior_metrics_capability.py for the full "one neutral
    response for every invalid-capability reason" contract, including a
    direct proof that this case's body is byte-identical to every other one.
    """
    application = _create_application(client)
    application_id = application["id"]
    capability = application["behavior_metrics_capability"]

    first = client.post(
        "/api/behavior-metrics",
        json={"application_id": application_id, "capability": capability, "time_on_page": 30},
    )
    assert first.status_code == 201

    duplicate = client.post(
        "/api/behavior-metrics",
        json={"application_id": application_id, "capability": capability, "time_on_page": 60},
    )
    assert duplicate.status_code == 403


def test_behavior_metric_for_missing_application_returns_neutral_403(client):
    """A nonexistent application_id must not get a distinct 404 - see the
    module-level comment on this: revealing "this id doesn't exist" via a
    different status than "wrong capability" is exactly the oracle Stage 1A
    correction 2 removes."""
    response = client.post(
        "/api/behavior-metrics",
        json={"application_id": 999999, "capability": "does-not-matter", "time_on_page": 10},
    )
    assert response.status_code == 403


def test_deleting_application_cascades_to_behavior_metric(client, admin_auth_headers):
    application = _create_application(client)
    application_id = application["id"]
    capability = application["behavior_metrics_capability"]
    metric_id = client.post(
        "/api/behavior-metrics",
        json={"application_id": application_id, "capability": capability, "time_on_page": 15},
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
