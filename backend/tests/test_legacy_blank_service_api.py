"""Integration tests for the Stage 1B correction covering a historical
AdminSetting row with a blank/whitespace-only service_name against a real
PostgreSQL test database.

Requires TEST_DATABASE_URL, exactly like test_api.py; the whole module is
skipped with an explicit reason if it's unset.

AdminSettingCreate/Update reject a blank service_name going forward (see
app/schemas/admin_setting.py and test_admin_setting_validation_api.py), but
a legacy database can still hold a row written before that validation
existed: `service_name = '   '`, `is_active = true`. This module seeds
exactly such a row directly at the DB level (bypassing the API/schema
entirely, the same way a real pre-Stage-1B deployment would have created
it) and proves three things independently:

- Administrative reads (GET /admin-settings, GET /admin-settings/{id})
  remain permissive - an operator must still be able to see and fix the
  row, so it must not become invisible or cause a 500.
- GET /admin-settings/active (the public service-selection source) excludes
  it - it must never be offered to a new applicant.
- POST /applications rejects it outright (422) even when called directly
  with the historical service's id, bypassing the active list entirely -
  see app/crud/application.py::_create_application_row's authoritative,
  locked re-check. No Application, no behavior-metrics capability, and no
  successful idempotency claim may ever be created against it.
"""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.security import hash_idempotency_key
from app.models.admin_setting import AdminSetting
from app.models.application import Application
from app.models.application_behavior_capability import ApplicationBehaviorCapability
from app.models.application_idempotency_key import ApplicationIdempotencyKey
from tests.db_safety_guard import get_test_database_url
from tests.test_api import _application_payload

TEST_DATABASE_URL = get_test_database_url()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set - skipping PostgreSQL integration tests",
)


def _seed_historical_blank_service(db_session) -> AdminSetting:
    """Bypasses AdminSettingCreate/crud.create_admin_setting entirely (direct
    ORM insert, simulating a row written before Stage 1B's required-text
    validation existed) - exactly the shape the independent finding
    describes: `service_name = '   '`, `is_active = true`."""
    setting = AdminSetting(
        service_name="   ",
        budget_min=Decimal("0.00"),
        budget_max=Decimal("100000.00"),
        is_active=True,
    )
    db_session.add(setting)
    db_session.commit()
    db_session.refresh(setting)
    return setting


# --- administrative compatibility: blank row stays visible/fixable ---------


def test_admin_list_still_returns_the_historical_blank_service(client, db_session, admin_auth_headers):
    historical = _seed_historical_blank_service(db_session)

    response = client.get("/api/admin-settings", headers=admin_auth_headers)
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()}
    assert historical.id in ids


def test_admin_get_still_returns_the_historical_blank_service(client, db_session, admin_auth_headers):
    historical = _seed_historical_blank_service(db_session)

    response = client.get(f"/api/admin-settings/{historical.id}", headers=admin_auth_headers)
    assert response.status_code == 200, response.text
    assert response.json()["service_name"] == "   "
    assert response.json()["is_active"] is True


# --- public active list: blank row excluded ---------------------------------


def test_active_list_excludes_the_historical_blank_service(client, db_session):
    historical = _seed_historical_blank_service(db_session)

    response = client.get("/api/admin-settings/active")
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()}
    assert historical.id not in ids


def test_active_list_still_includes_a_normal_valid_active_service(
    client, db_session, admin_auth_headers
):
    """The filter must be narrowly scoped to blank/whitespace names - a
    legitimate active service must keep working exactly as before."""
    _seed_historical_blank_service(db_session)

    valid = client.post(
        "/api/admin-settings",
        json={
            "service_name": "Legit Active Service",
            "budget_min": "100.00",
            "budget_max": "1000.00",
            "is_active": True,
        },
        headers=admin_auth_headers,
    )
    assert valid.status_code == 201, valid.text
    valid_id = valid.json()["id"]

    response = client.get("/api/admin-settings/active")
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()}
    assert valid_id in ids


# --- public application creation: authoritative rejection at the locked check --


def test_direct_application_create_against_blank_service_is_rejected(client, db_session):
    """A raw client that already has (or guesses) the historical service's
    id, bypassing GET /admin-settings/active entirely, must still be
    rejected - the active-list filter alone is not the authoritative
    control."""
    historical = _seed_historical_blank_service(db_session)

    applications_before = db_session.scalars(select(Application.id)).all()
    capabilities_before = db_session.scalars(select(ApplicationBehaviorCapability.id)).all()

    response = client.post(
        "/api/applications",
        json=_application_payload(service_id=historical.id, budget="500.00"),
    )
    assert response.status_code == 422, response.text

    applications_after = db_session.scalars(select(Application.id)).all()
    capabilities_after = db_session.scalars(select(ApplicationBehaviorCapability.id)).all()
    assert applications_after == applications_before
    assert capabilities_after == capabilities_before


def test_direct_application_create_against_blank_service_leaves_no_idempotency_claim(
    client, db_session
):
    """Even under an Idempotency-Key, a validation failure must never leave
    a "claimed but unfulfilled" key behind (see
    app/crud/application.py::create_application_idempotent's module-level
    design note) - a blank-service rejection is exactly such a failure."""
    historical = _seed_historical_blank_service(db_session)
    key = "B" * 43

    response = client.post(
        "/api/applications",
        json=_application_payload(service_id=historical.id, budget="500.00"),
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 422, response.text

    mapping_rows = db_session.scalars(
        select(ApplicationIdempotencyKey).where(
            ApplicationIdempotencyKey.idempotency_key_hash == hash_idempotency_key(key)
        )
    ).all()
    assert mapping_rows == []

    # The same key is free to use for a legitimate follow-up request - the
    # failed attempt claimed nothing.
    retry = client.post(
        "/api/applications",
        json=_application_payload(budget="1000.00"),
        headers={"Idempotency-Key": key},
    )
    assert retry.status_code == 201, retry.text


def test_valid_active_service_still_creates_applications_normally(
    client, db_session, admin_auth_headers
):
    """Sanity check alongside the rejection above: the correction targets
    only blank/whitespace service names, not service creation in general."""
    _seed_historical_blank_service(db_session)

    valid = client.post(
        "/api/admin-settings",
        json={
            "service_name": "Legit Active Service",
            "budget_min": "100.00",
            "budget_max": "1000.00",
            "is_active": True,
        },
        headers=admin_auth_headers,
    )
    assert valid.status_code == 201, valid.text
    valid_id = valid.json()["id"]

    response = client.post(
        "/api/applications",
        json=_application_payload(service_id=valid_id, budget="500.00"),
    )
    assert response.status_code == 201, response.text
    assert response.json()["interested_product"] == "Legit Active Service"
