"""Integration tests for GET /api/applications/prioritized against a real
PostgreSQL test database.

Requires TEST_DATABASE_URL, exactly like test_api.py; the whole module is
skipped with an explicit reason if it's unset. The scoring rules themselves
are unit-tested in test_application_scoring.py without a database; this
file focuses on the HTTP/DB wiring - auth, response shape, sort order and
pagination.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.application import Application
from tests.db_safety_guard import get_test_database_url
from tests.test_api import _application_payload

TEST_DATABASE_URL = get_test_database_url()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set - skipping PostgreSQL integration tests",
)


def _insert_application(db_session, created_at: datetime | None = None, **overrides) -> Application:
    """Insert an Application directly via the ORM, bypassing the public API.

    Used only to pin an exact created_at for sort-order tests - the public
    POST endpoint has no way to control that timestamp, and it is
    otherwise driven purely by DB server time within the test's savepoint.
    """
    payload = _application_payload(**overrides)
    payload["budget"] = Decimal(str(payload["budget"]))
    # Bypasses ApplicationCreate/crud.create_application entirely (direct
    # ORM insert), so interested_product - normally derived server-side
    # from the looked-up service (see app/crud/application.py) - must be
    # set explicitly here.
    payload["interested_product"] = "Test Default Service"
    application = Application(**payload)
    if created_at is not None:
        application.created_at = created_at
    db_session.add(application)
    db_session.commit()
    db_session.refresh(application)
    return application


def test_prioritized_requires_token(client):
    response = client.get("/api/applications/prioritized")
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


def test_prioritized_rejects_corrupted_token(client):
    response = client.get(
        "/api/applications/prioritized", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert response.status_code == 401


def test_prioritized_with_valid_admin_token_returns_200(client, admin_auth_headers):
    client.post("/api/applications", json=_application_payload())
    response = client.get("/api/applications/prioritized", headers=admin_auth_headers)
    assert response.status_code == 200


def test_prioritized_response_matches_schema(client, admin_auth_headers):
    client.post("/api/applications", json=_application_payload())
    response = client.get("/api/applications/prioritized", headers=admin_auth_headers)
    assert response.status_code == 200
    body = response.json()

    assert set(body.keys()) == {"items", "total", "skip", "limit"}
    assert body["skip"] == 0
    assert body["limit"] == 100
    assert body["total"] >= 1
    assert len(body["items"]) >= 1

    item = body["items"][0]
    assert set(item.keys()) == {
        "application",
        "priority_score",
        "priority_level",
        "priority_label",
        "reasons",
        "recommended_action",
        "recommended_team",
        "requires_personal_manager",
    }
    assert isinstance(item["priority_score"], int)
    assert 0 <= item["priority_score"] <= 100
    assert item["priority_level"] in {"hot", "medium", "low"}
    assert isinstance(item["reasons"], list)
    for reason in item["reasons"]:
        assert set(reason.keys()) == {"code", "points", "label"}
        assert isinstance(reason["points"], int)
    assert isinstance(item["application"]["id"], int)
    assert "password_hash" not in response.text


def test_prioritized_sorts_by_score_descending(client, admin_auth_headers):
    low_id = client.post("/api/applications", json=_application_payload()).json()["id"]
    hot_id = client.post(
        "/api/applications",
        json=_application_payload(
            deadline="Как можно скорее",
            budget="100000.00",
            business_size="Более 20 автомобилей",
            task_scope="Обслуживание автопарка",
            task_type="Диагностика или ремонт",
            business_niche="Автопарк компании",
            requester_role="Управляющий автопарком",
            business_info="Авария, нужен эвакуатор",
        ),
    ).json()["id"]

    response = client.get("/api/applications/prioritized", headers=admin_auth_headers)
    items = response.json()["items"]
    ids_in_order = [item["application"]["id"] for item in items]

    assert ids_in_order.index(hot_id) < ids_in_order.index(low_id)
    scores = [item["priority_score"] for item in items]
    assert scores == sorted(scores, reverse=True)


def test_prioritized_tie_break_by_older_created_at_first(client, admin_auth_headers, db_session):
    now = datetime.now(timezone.utc)
    # Insertion order (and thus id order) is deliberately the opposite of
    # created_at order, so a passing test proves created_at drives the tie
    # break rather than id happening to already match it.
    newer_but_lower_id = _insert_application(db_session, created_at=now)
    older_but_higher_id = _insert_application(db_session, created_at=now - timedelta(days=1))
    assert newer_but_lower_id.id < older_but_higher_id.id

    response = client.get("/api/applications/prioritized", headers=admin_auth_headers)
    items = response.json()["items"]
    ids_in_order = [item["application"]["id"] for item in items]

    assert ids_in_order.index(older_but_higher_id.id) < ids_in_order.index(newer_but_lower_id.id)


def test_prioritized_full_tie_break_by_id_ascending(client, admin_auth_headers, db_session):
    same_created_at = datetime.now(timezone.utc)
    first = _insert_application(db_session, created_at=same_created_at)
    second = _insert_application(db_session, created_at=same_created_at)

    response = client.get("/api/applications/prioritized", headers=admin_auth_headers)
    items = response.json()["items"]
    ids_in_order = [item["application"]["id"] for item in items]

    assert ids_in_order.index(first.id) < ids_in_order.index(second.id)


def test_prioritized_skip_and_limit_apply_after_scoring_and_sorting(client, admin_auth_headers):
    for i in range(5):
        client.post("/api/applications", json=_application_payload(budget=str(1000 * (i + 1))))

    full_response = client.get("/api/applications/prioritized", headers=admin_auth_headers)
    full_ids_in_order = [item["application"]["id"] for item in full_response.json()["items"]]

    paged_response = client.get(
        "/api/applications/prioritized", params={"skip": 2, "limit": 2}, headers=admin_auth_headers
    )
    paged_body = paged_response.json()
    assert paged_body["skip"] == 2
    assert paged_body["limit"] == 2
    assert len(paged_body["items"]) == 2

    paged_ids = [item["application"]["id"] for item in paged_body["items"]]
    assert paged_ids == full_ids_in_order[2:4]
    assert paged_body["total"] == full_response.json()["total"]


def test_prioritized_total_matches_full_application_count(client, admin_auth_headers):
    for _ in range(3):
        client.post("/api/applications", json=_application_payload())

    response = client.get(
        "/api/applications/prioritized", params={"limit": 1}, headers=admin_auth_headers
    )
    body = response.json()
    assert len(body["items"]) == 1
    assert body["total"] >= 3


@pytest.mark.parametrize("params", [{"skip": -1}, {"limit": 0}, {"limit": 101}])
def test_prioritized_pagination_rejects_invalid_params(client, admin_auth_headers, params):
    response = client.get(
        "/api/applications/prioritized", params=params, headers=admin_auth_headers
    )
    assert response.status_code == 422


def test_prioritized_does_not_500_on_unknown_legacy_values(
    client, admin_auth_headers, db_session
):
    """Stage 1B made business_niche/company_size/deadline/etc. closed
    Literal enums (see app/schemas/application.py), so the public API can no
    longer be used to store an unknown/legacy value for them (see
    tests/test_api.py::test_application_create_rejects_invalid_categorical_value).
    The scoring/listing pipeline must still never 500 on such a value,
    though - e.g. a row inserted directly (bypassing the API) before this
    stage, by a maintenance script, or by a future relaxation of the enum -
    so this now inserts directly via the ORM rather than through POST, and
    keeps proving the defensive property score_application/list-prioritized
    already guarantees for any string (see
    test_application_scoring.py::test_unknown_and_legacy_values_do_not_raise_and_score_zero)."""
    payload = _application_payload(
        deadline="Совершенно неизвестное значение",
        business_size="Индивидуальный предприниматель",
        task_scope="Пробный проект",
        task_type="Разработка с нуля",
        business_niche="Другое",
        requester_role="Сотрудник",
    )
    payload["budget"] = Decimal(str(payload["budget"]))
    payload["interested_product"] = "Test Default Service"
    db_session.add(Application(**payload))
    db_session.commit()

    response = client.get("/api/applications/prioritized", headers=admin_auth_headers)
    assert response.status_code == 200


def test_create_application_stays_public_alongside_prioritized_endpoint(client):
    response = client.post("/api/applications", json=_application_payload())
    assert response.status_code == 201


def test_list_applications_endpoint_keeps_previous_contract(client, admin_auth_headers):
    client.post("/api/applications", json=_application_payload())
    response = client.get("/api/applications", headers=admin_auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert "priority_score" not in body[0]
    assert set(body[0].keys()) == {
        "id",
        "first_name",
        "last_name",
        "middle_name",
        "contact_data",
        "business_niche",
        "company_size",
        "business_info",
        "task_scope",
        "requester_role",
        "business_size",
        "need_scope",
        "deadline",
        "task_type",
        "service_id",
        "interested_product",
        "budget",
        "preferred_contact_method",
        "preferred_contact_time",
        "comment",
        "created_at",
        "updated_at",
    }
