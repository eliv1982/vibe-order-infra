"""Integration tests for GET /api/analytics/overview against a real
PostgreSQL test database.

Requires TEST_DATABASE_URL, exactly like test_api.py; the whole module is
skipped with an explicit reason if it's unset. The aggregation logic itself
is unit-tested without a database in test_behavior_analytics_service.py;
this file focuses on the HTTP/DB wiring - auth, period query handling,
response shape, and that DB-backed data lines up with what the pure
service computes.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.application import Application
from app.models.behavior_metric import BehaviorMetric
from tests.db_safety_guard import get_test_database_url
from tests.test_api import _application_payload

TEST_DATABASE_URL = get_test_database_url()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set - skipping PostgreSQL integration tests",
)


def _insert_application(db_session, created_at: datetime | None = None, **overrides) -> Application:
    """Insert an Application directly via the ORM, bypassing the public API -
    used only to pin an exact created_at (mirrors test_application_prioritization_api.py)."""
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


def _insert_behavior_metric(
    db_session,
    application_id: int,
    created_at: datetime | None = None,
    time_on_page: int = 0,
    clicked_buttons=None,
    cursor_hover_data=None,
    return_count: int = 0,
) -> BehaviorMetric:
    metric = BehaviorMetric(
        application_id=application_id,
        time_on_page=time_on_page,
        clicked_buttons=clicked_buttons if clicked_buttons is not None else [],
        cursor_hover_data=cursor_hover_data if cursor_hover_data is not None else {},
        return_count=return_count,
    )
    if created_at is not None:
        metric.created_at = created_at
    db_session.add(metric)
    db_session.commit()
    db_session.refresh(metric)
    return metric


# --- auth ------------------------------------------------------------------


def test_overview_requires_token(client):
    response = client.get("/api/analytics/overview")
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


def test_overview_rejects_corrupted_token(client):
    response = client.get(
        "/api/analytics/overview", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert response.status_code == 401


def test_overview_valid_admin_token_returns_200(client, admin_auth_headers):
    response = client.get("/api/analytics/overview", headers=admin_auth_headers)
    assert response.status_code == 200


# --- period query param ------------------------------------------------


def test_overview_default_period_is_week(client, admin_auth_headers):
    response = client.get("/api/analytics/overview", headers=admin_auth_headers)
    assert response.json()["period"] == "week"


@pytest.mark.parametrize("period", ["day", "week", "month"])
def test_overview_accepts_valid_periods(client, admin_auth_headers, period):
    response = client.get(f"/api/analytics/overview?period={period}", headers=admin_auth_headers)
    assert response.status_code == 200
    assert response.json()["period"] == period


def test_overview_invalid_period_returns_422(client, admin_auth_headers):
    response = client.get("/api/analytics/overview?period=year", headers=admin_auth_headers)
    assert response.status_code == 422


# --- empty period ------------------------------------------------------


def test_overview_empty_period_returns_zeroed_response(client, admin_auth_headers):
    response = client.get("/api/analytics/overview", headers=admin_auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["applications_count"] == 0
    assert body["metrics_count"] == 0
    assert body["applications_with_metrics"] == 0
    assert body["applications_without_metrics"] == 0
    assert body["popular_buttons"] == []
    assert body["section_activity"] == []
    assert body["average_time_on_page_seconds"] is None
    assert body["median_time_on_page_seconds"] is None
    assert body["average_return_count"] is None
    assert body["total_return_count"] == 0
    assert body["total_button_clicks"] == 0
    assert body["unique_clicked_buttons"] == 0


# --- counts and aggregates ------------------------------------------------


def test_overview_counts_applications_and_metrics_in_period(client, admin_auth_headers, db_session):
    application = _insert_application(db_session)
    _insert_behavior_metric(
        db_session,
        application.id,
        time_on_page=30,
        return_count=2,
        clicked_buttons=[{"button": "cta", "count": 4}],
        cursor_hover_data={"hero": {"hovers": 1, "ms": 2000}},
    )

    response = client.get("/api/analytics/overview", headers=admin_auth_headers)
    body = response.json()
    assert body["applications_count"] == 1
    assert body["metrics_count"] == 1
    assert body["applications_with_metrics"] == 1
    assert body["applications_without_metrics"] == 0
    assert body["average_time_on_page_seconds"] == 30.0
    assert body["median_time_on_page_seconds"] == 30.0
    assert body["average_return_count"] == 2.0
    assert body["total_return_count"] == 2
    assert body["total_button_clicks"] == 4
    assert body["unique_clicked_buttons"] == 1
    assert body["popular_buttons"][0]["name"] == "cta"
    assert body["popular_buttons"][0]["count"] == 4
    assert body["popular_buttons"][0]["share_percent"] == 100.0
    assert body["section_activity"][0]["section"] == "hero"
    assert body["section_activity"][0]["total_duration_seconds"] == 2.0
    assert body["section_activity"][0]["interactions_count"] == 1


def test_overview_application_without_metrics_is_counted(client, admin_auth_headers, db_session):
    _insert_application(db_session)

    response = client.get("/api/analytics/overview", headers=admin_auth_headers)
    body = response.json()
    assert body["applications_count"] == 1
    assert body["applications_with_metrics"] == 0
    assert body["applications_without_metrics"] == 1


def test_overview_data_outside_period_is_excluded(client, admin_auth_headers, db_session):
    old = datetime.now(timezone.utc) - timedelta(days=40)
    application = _insert_application(db_session, created_at=old)
    _insert_behavior_metric(db_session, application.id, created_at=old, time_on_page=99)

    response = client.get("/api/analytics/overview?period=month", headers=admin_auth_headers)
    body = response.json()
    assert body["applications_count"] == 0
    assert body["metrics_count"] == 0
    assert body["average_time_on_page_seconds"] is None


def test_overview_button_aggregation_across_applications(client, admin_auth_headers, db_session):
    app1 = _insert_application(db_session)
    app2 = _insert_application(db_session)
    _insert_behavior_metric(db_session, app1.id, clicked_buttons=[{"button": "cta", "count": 2}])
    _insert_behavior_metric(
        db_session,
        app2.id,
        clicked_buttons=[{"button": "cta", "count": 3}, {"button": "other", "count": 1}],
    )

    response = client.get("/api/analytics/overview", headers=admin_auth_headers)
    body = response.json()
    assert body["total_button_clicks"] == 6
    assert body["unique_clicked_buttons"] == 2
    assert body["popular_buttons"][0]["name"] == "cta"
    assert body["popular_buttons"][0]["count"] == 5


def test_overview_section_aggregation_across_applications(client, admin_auth_headers, db_session):
    app1 = _insert_application(db_session)
    app2 = _insert_application(db_session)
    _insert_behavior_metric(db_session, app1.id, cursor_hover_data={"hero": {"hovers": 1, "ms": 1000}})
    _insert_behavior_metric(db_session, app2.id, cursor_hover_data={"hero": {"hovers": 1, "ms": 2000}})

    response = client.get("/api/analytics/overview", headers=admin_auth_headers)
    body = response.json()
    hero = next(item for item in body["section_activity"] if item["section"] == "hero")
    assert hero["interactions_count"] == 2
    assert hero["total_duration_seconds"] == 3.0


# --- response shape ----------------------------------------------------


def test_overview_response_matches_schema_shape(client, admin_auth_headers):
    response = client.get("/api/analytics/overview", headers=admin_auth_headers)
    body = response.json()
    expected_keys = {
        "period",
        "period_start",
        "period_end",
        "applications_count",
        "metrics_count",
        "applications_with_metrics",
        "applications_without_metrics",
        "average_time_on_page_seconds",
        "median_time_on_page_seconds",
        "average_return_count",
        "total_return_count",
        "total_button_clicks",
        "unique_clicked_buttons",
        "popular_buttons",
        "section_activity",
    }
    assert set(body.keys()) == expected_keys


def test_overview_period_bounds_are_timezone_aware(client, admin_auth_headers):
    response = client.get("/api/analytics/overview", headers=admin_auth_headers)
    body = response.json()
    parsed_start = datetime.fromisoformat(body["period_start"].replace("Z", "+00:00"))
    parsed_end = datetime.fromisoformat(body["period_end"].replace("Z", "+00:00"))
    assert parsed_start.tzinfo is not None
    assert parsed_end.tzinfo is not None


# --- privacy -----------------------------------------------------------


def test_overview_does_not_leak_pii(client, admin_auth_headers, db_session):
    application = _insert_application(
        db_session,
        first_name="SecretFirst",
        last_name="SecretLast",
        contact_data="secret@example.com",
        business_info="Confidential business info",
        need_scope="Confidential need scope",
        comment="Confidential comment",
    )
    _insert_behavior_metric(db_session, application.id)

    response = client.get("/api/analytics/overview", headers=admin_auth_headers)
    body_text = response.text
    for leaked_value in (
        "SecretFirst",
        "SecretLast",
        "secret@example.com",
        "Confidential business info",
        "Confidential need scope",
        "Confidential comment",
    ):
        assert leaked_value not in body_text
    for leaked_field in ("contact_data", "first_name", "last_name", "business_info", "need_scope", "comment"):
        assert leaked_field not in body_text
