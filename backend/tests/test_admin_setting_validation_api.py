"""Integration tests for authoritative backend validation of AdminSetting.
service_name (Stage 1B, section E) against a real PostgreSQL test database.

Requires TEST_DATABASE_URL, exactly like test_api.py; the whole module is
skipped with an explicit reason if it's unset. Before this correction,
service_name was enforced only by `max_length` - "" or "   " passed schema
validation and was stored verbatim, then denormalized as-is into every new
Application's interested_product (see app/crud/application.py) - so a
blank/whitespace service is what let a public application end up with a
blank server-derived product name. These tests exercise the API directly
with payloads a direct HTTP client could send.
"""

import pytest

from tests.db_safety_guard import get_test_database_url
from tests.test_api import _application_payload

TEST_DATABASE_URL = get_test_database_url()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set - skipping PostgreSQL integration tests",
)


def _setting_payload(**overrides) -> dict:
    payload = {
        "service_name": "Ceramic Coating",
        "budget_min": "1000.00",
        "budget_max": "5000.00",
        "is_active": True,
    }
    payload.update(overrides)
    return payload


# --- create: empty / whitespace-only ----------------------------------------


@pytest.mark.parametrize("value", ["", "   ", "\t\n  "])
def test_create_rejects_empty_or_whitespace_only_service_name(client, admin_auth_headers, value):
    response = client.post(
        "/api/admin-settings", json=_setting_payload(service_name=value), headers=admin_auth_headers
    )
    assert response.status_code == 422, response.text


def test_create_strips_leading_trailing_whitespace(client, admin_auth_headers):
    response = client.post(
        "/api/admin-settings",
        json=_setting_payload(service_name="  Ceramic Coating  "),
        headers=admin_auth_headers,
    )
    assert response.status_code == 201, response.text
    assert response.json()["service_name"] == "Ceramic Coating"


def test_create_accepts_exactly_255_characters(client, admin_auth_headers):
    response = client.post(
        "/api/admin-settings",
        json=_setting_payload(service_name="x" * 255),
        headers=admin_auth_headers,
    )
    assert response.status_code == 201, response.text


def test_create_rejects_256_characters(client, admin_auth_headers):
    response = client.post(
        "/api/admin-settings",
        json=_setting_payload(service_name="x" * 256),
        headers=admin_auth_headers,
    )
    assert response.status_code == 422, response.text


# --- update: empty / whitespace-only ----------------------------------------


def _create_setting(client, admin_auth_headers, **overrides) -> dict:
    response = client.post(
        "/api/admin-settings", json=_setting_payload(**overrides), headers=admin_auth_headers
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize("value", ["", "   ", "\t\n  "])
def test_update_rejects_empty_or_whitespace_only_service_name(client, admin_auth_headers, value):
    setting = _create_setting(client, admin_auth_headers)
    response = client.patch(
        f"/api/admin-settings/{setting['id']}",
        json={"service_name": value},
        headers=admin_auth_headers,
    )
    assert response.status_code == 422, response.text


def test_update_strips_leading_trailing_whitespace(client, admin_auth_headers):
    setting = _create_setting(client, admin_auth_headers)
    response = client.patch(
        f"/api/admin-settings/{setting['id']}",
        json={"service_name": "  Renamed Service  "},
        headers=admin_auth_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["service_name"] == "Renamed Service"


def test_update_accepts_exactly_255_characters(client, admin_auth_headers):
    setting = _create_setting(client, admin_auth_headers)
    response = client.patch(
        f"/api/admin-settings/{setting['id']}",
        json={"service_name": "y" * 255},
        headers=admin_auth_headers,
    )
    assert response.status_code == 200, response.text


def test_update_rejects_256_characters(client, admin_auth_headers):
    setting = _create_setting(client, admin_auth_headers)
    response = client.patch(
        f"/api/admin-settings/{setting['id']}",
        json={"service_name": "y" * 256},
        headers=admin_auth_headers,
    )
    assert response.status_code == 422, response.text


# --- end-to-end: a blank service name can no longer poison interested_product --


def test_a_blank_service_can_no_longer_be_created_to_poison_interested_product(
    client, admin_auth_headers
):
    """Before this correction, POST /admin-settings with a blank
    service_name succeeded, and every Application created against it then
    inherited a blank interested_product server-side (see
    app/crud/application.py::_create_application_row). Proving service_name
    creation itself now rejects blank/whitespace values closes off that
    chain entirely - there is no longer any service a public application
    could reference to obtain one."""
    response = client.post(
        "/api/admin-settings", json=_setting_payload(service_name="   "), headers=admin_auth_headers
    )
    assert response.status_code == 422, response.text

    # Sanity: the failed service was never created, so there's nothing a
    # public application submission could even reference.
    listing = client.get("/api/admin-settings", headers=admin_auth_headers)
    assert all(item["service_name"].strip() for item in listing.json())
