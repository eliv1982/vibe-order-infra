"""Integration tests for authoritative backend validation on
POST /applications (Stage 1B, section B) against a real PostgreSQL test
database.

Requires TEST_DATABASE_URL, exactly like test_api.py; the whole module is
skipped with an explicit reason if it's unset. These exercise the API
directly with payloads a direct HTTP client could send, deliberately never
relying on any frontend-side validation - every case here bypassed the
frontend entirely before this stage's backend hardening.
"""

from decimal import Decimal

import pytest

from tests.db_safety_guard import get_test_database_url
from tests.test_api import _application_payload

TEST_DATABASE_URL = get_test_database_url()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set - skipping PostgreSQL integration tests",
)


def _create_service(client, admin_auth_headers, **overrides) -> dict:
    payload = {
        "service_name": "Validation Test Service",
        "budget_min": "1000.00",
        "budget_max": "5000.00",
        "is_active": True,
    }
    payload.update(overrides)
    response = client.post("/api/admin-settings", json=payload, headers=admin_auth_headers)
    assert response.status_code == 201, response.text
    return response.json()


# --- required text fields: empty / whitespace-only ------------------------


@pytest.mark.parametrize("field", ["first_name", "last_name", "contact_data", "business_info", "need_scope"])
@pytest.mark.parametrize("value", ["", "   ", "\t\n  "])
def test_application_rejects_empty_or_whitespace_only_required_text(client, field, value):
    response = client.post("/api/applications", json=_application_payload(**{field: value}))
    assert response.status_code == 422, response.text


def test_application_strips_leading_trailing_whitespace_from_required_text(client):
    response = client.post(
        "/api/applications", json=_application_payload(first_name="  Ivan  ")
    )
    assert response.status_code == 201, response.text
    assert response.json()["first_name"] == "Ivan"


def test_application_normalizes_blank_optional_field_to_null(client):
    response = client.post(
        "/api/applications", json=_application_payload(middle_name="   ", comment="\t")
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["middle_name"] is None
    assert body["comment"] is None


# --- required text fields: oversized -------------------------------------


@pytest.mark.parametrize(
    "field, max_length",
    [
        ("first_name", 100),
        ("last_name", 100),
        ("contact_data", 255),
        ("business_info", 4000),
        ("need_scope", 4000),
    ],
)
def test_application_rejects_oversized_required_text(client, field, max_length):
    response = client.post(
        "/api/applications", json=_application_payload(**{field: "x" * (max_length + 1)})
    )
    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    "field, max_length",
    [("first_name", 100), ("contact_data", 255), ("business_info", 4000), ("need_scope", 4000)],
)
def test_application_accepts_exactly_max_length_required_text(client, field, max_length):
    response = client.post(
        "/api/applications", json=_application_payload(**{field: "x" * max_length})
    )
    assert response.status_code == 201, response.text


def test_application_rejects_oversized_comment(client):
    response = client.post("/api/applications", json=_application_payload(comment="x" * 2001))
    assert response.status_code == 422


def test_application_rejects_oversized_middle_name(client):
    response = client.post("/api/applications", json=_application_payload(middle_name="x" * 101))
    assert response.status_code == 422


# --- categorical values -----------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "business_niche",
        "company_size",
        "business_size",
        "requester_role",
        "task_scope",
        "task_type",
        "deadline",
        "preferred_contact_method",
        "preferred_contact_time",
    ],
)
def test_application_rejects_invalid_categorical_value(client, field):
    response = client.post(
        "/api/applications", json=_application_payload(**{field: "Совершенно произвольное значение"})
    )
    assert response.status_code == 422, response.text


def test_application_rejects_empty_string_for_categorical_field(client):
    response = client.post("/api/applications", json=_application_payload(business_niche=""))
    assert response.status_code == 422


# --- service selection: existence / active state ----------------------


def test_application_rejects_nonexistent_service(client):
    response = client.post("/api/applications", json=_application_payload(service_id=999999))
    assert response.status_code == 422, response.text
    assert "sql" not in response.text.lower()


def test_application_rejects_inactive_service(client, admin_auth_headers):
    service = _create_service(client, admin_auth_headers, is_active=False)
    response = client.post(
        "/api/applications",
        json=_application_payload(service_id=service["id"], budget="2000.00"),
    )
    assert response.status_code == 422


def test_application_rejects_negative_service_id(client):
    response = client.post("/api/applications", json=_application_payload(service_id=-1))
    assert response.status_code == 422


def test_application_rejects_zero_service_id(client):
    response = client.post("/api/applications", json=_application_payload(service_id=0))
    assert response.status_code == 422


def test_application_ignores_client_supplied_interested_product(client, admin_auth_headers):
    """The client can no longer invent a service name via interested_product
    - the field is derived server-side from the looked-up service_id and any
    client-supplied value for it is simply not part of the accepted schema
    (an unknown field is ignored, not an error, matching ordinary Pydantic
    behavior for extra fields)."""
    service = _create_service(client, admin_auth_headers, service_name="Real Service")
    payload = _application_payload(service_id=service["id"], budget="2000.00")
    payload["interested_product"] = "Invented Service Name"
    response = client.post("/api/applications", json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["interested_product"] == "Real Service"


# --- budget / service correlation --------------------------------------


def test_application_rejects_budget_below_service_minimum(client, admin_auth_headers):
    service = _create_service(client, admin_auth_headers, budget_min="1000.00", budget_max="5000.00")
    response = client.post(
        "/api/applications",
        json=_application_payload(service_id=service["id"], budget="999.99"),
    )
    assert response.status_code == 422


def test_application_rejects_budget_above_service_maximum(client, admin_auth_headers):
    service = _create_service(client, admin_auth_headers, budget_min="1000.00", budget_max="5000.00")
    response = client.post(
        "/api/applications",
        json=_application_payload(service_id=service["id"], budget="5000.01"),
    )
    assert response.status_code == 422


def test_application_accepts_budget_at_service_minimum_boundary(client, admin_auth_headers):
    service = _create_service(client, admin_auth_headers, budget_min="1000.00", budget_max="5000.00")
    response = client.post(
        "/api/applications",
        json=_application_payload(service_id=service["id"], budget="1000.00"),
    )
    assert response.status_code == 201, response.text


def test_application_accepts_budget_at_service_maximum_boundary(client, admin_auth_headers):
    service = _create_service(client, admin_auth_headers, budget_min="1000.00", budget_max="5000.00")
    response = client.post(
        "/api/applications",
        json=_application_payload(service_id=service["id"], budget="5000.00"),
    )
    assert response.status_code == 201, response.text


def test_application_accepts_budget_equal_to_min_equal_to_max(client, admin_auth_headers):
    service = _create_service(client, admin_auth_headers, budget_min="2500.00", budget_max="2500.00")
    response = client.post(
        "/api/applications",
        json=_application_payload(service_id=service["id"], budget="2500.00"),
    )
    assert response.status_code == 201, response.text


def test_application_rejects_negative_budget(client):
    response = client.post("/api/applications", json=_application_payload(budget="-1.00"))
    assert response.status_code == 422


def test_deleting_a_service_with_existing_applications_is_blocked(client, admin_auth_headers):
    """Application.service_id is a plain FK with no ON DELETE CASCADE (see
    app/models/application.py) - a service that already has applications
    referencing it can never be deleted out from under them, converging on
    the same 409 the rest of this codebase already uses for integrity
    conflicts (see app/crud/admin_setting.py)."""
    service = _create_service(client, admin_auth_headers)
    create_resp = client.post(
        "/api/applications",
        json=_application_payload(service_id=service["id"], budget="2000.00"),
    )
    assert create_resp.status_code == 201

    delete_resp = client.delete(f"/api/admin-settings/{service['id']}", headers=admin_auth_headers)
    assert delete_resp.status_code == 409


# --- numeric/database bounds: NUMERIC(12, 2) overflow ----------------------


def test_application_rejects_budget_with_too_many_integer_digits(client):
    # NUMERIC(12, 2) allows at most 12 significant digits total (10 before
    # the decimal point + 2 after) - this value has 13, so Pydantic's
    # max_digits bound (see app/schemas/application.py) rejects it before
    # the service lookup/budget-range check ever runs; the default test
    # service's own wide-open range is irrelevant here.
    response = client.post("/api/applications", json=_application_payload(budget="99999999999.99"))
    assert response.status_code == 422, response.text
    assert response.status_code != 500


def test_application_rejects_budget_with_excess_decimal_scale(client, admin_auth_headers):
    service = _create_service(client, admin_auth_headers, budget_min="1000.00", budget_max="5000.00")
    response = client.post(
        "/api/applications",
        json=_application_payload(service_id=service["id"], budget="1000.001"),
    )
    assert response.status_code == 422, response.text
    assert response.status_code != 500


def test_application_rejects_absurdly_large_budget_string(client):
    """A pathologically large numeric literal must be a validation error,
    never an unhandled SQLAlchemy DataError / HTTP 500."""
    response = client.post(
        "/api/applications", json=_application_payload(budget="9" * 50 + ".00")
    )
    assert response.status_code == 422, response.text
    assert response.status_code != 500


# --- admin PATCH: interested_product required-text validation --------------
#
# interested_product is admin-only/free-text on ApplicationUpdate (no
# service_id field on PATCH - see app/schemas/application.py) - before this
# correction it was enforced only by `max_length`, so an authenticated admin
# client could PATCH it to "" or "   ".


def test_patch_rejects_empty_or_whitespace_only_interested_product(client, admin_auth_headers):
    application_id = client.post("/api/applications", json=_application_payload()).json()["id"]
    for value in ("", "   ", "\t\n  "):
        response = client.patch(
            f"/api/applications/{application_id}",
            json={"interested_product": value},
            headers=admin_auth_headers,
        )
        assert response.status_code == 422, (value, response.text)


def test_patch_strips_leading_trailing_whitespace_from_interested_product(client, admin_auth_headers):
    application_id = client.post("/api/applications", json=_application_payload()).json()["id"]
    response = client.patch(
        f"/api/applications/{application_id}",
        json={"interested_product": "  Renamed Product  "},
        headers=admin_auth_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["interested_product"] == "Renamed Product"


def test_patch_accepts_exactly_255_characters_for_interested_product(client, admin_auth_headers):
    application_id = client.post("/api/applications", json=_application_payload()).json()["id"]
    response = client.patch(
        f"/api/applications/{application_id}",
        json={"interested_product": "x" * 255},
        headers=admin_auth_headers,
    )
    assert response.status_code == 200, response.text


def test_patch_rejects_256_characters_for_interested_product(client, admin_auth_headers):
    application_id = client.post("/api/applications", json=_application_payload()).json()["id"]
    response = client.patch(
        f"/api/applications/{application_id}",
        json={"interested_product": "x" * 256},
        headers=admin_auth_headers,
    )
    assert response.status_code == 422, response.text


# --- no 500s for client-controlled validation failures ---------------------


def test_application_none_of_the_bad_inputs_ever_500(client, admin_auth_headers):
    service = _create_service(client, admin_auth_headers, budget_min="1000.00", budget_max="5000.00")
    bad_payloads = [
        _application_payload(first_name=""),
        _application_payload(business_niche="not-a-real-option"),
        _application_payload(service_id=999999),
        _application_payload(service_id=service["id"], budget="1.00"),
        _application_payload(budget="9" * 40),
        _application_payload(first_name="x" * 500),
    ]
    for payload in bad_payloads:
        response = client.post("/api/applications", json=payload)
        assert response.status_code < 500, (payload, response.status_code, response.text)
        assert "traceback" not in response.text.lower()
