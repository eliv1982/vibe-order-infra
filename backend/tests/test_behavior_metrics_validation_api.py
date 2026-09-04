"""Integration tests for authoritative backend validation on
POST /behavior-metrics (Stage 1B, section B.5/B.6) against a real PostgreSQL
test database.

Requires TEST_DATABASE_URL, exactly like test_api.py; the whole module is
skipped with an explicit reason if it's unset. Capability-flow behavior
itself is covered exhaustively in test_behavior_metrics_capability.py; this
file focuses on the structural/numeric-bounds validation added in Stage 1B
for clicked_buttons/cursor_hover_data/time_on_page/return_count.
"""

import pytest

from tests.db_safety_guard import get_test_database_url
from tests.test_api import _application_payload

TEST_DATABASE_URL = get_test_database_url()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set - skipping PostgreSQL integration tests",
)


def _create_application(client, **overrides) -> dict:
    response = client.post("/api/applications", json=_application_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def _submit(client, application_id, capability, **overrides) -> object:
    payload = {"application_id": application_id, "capability": capability}
    payload.update(overrides)
    return client.post("/api/behavior-metrics", json=payload)


# --- PostgreSQL INTEGER overflow -------------------------------------------


def test_time_on_page_rejects_value_beyond_postgres_int4_range(client):
    application = _create_application(client)
    response = _submit(
        client,
        application["id"],
        application["behavior_metrics_capability"],
        time_on_page=99_999_999_999,
    )
    assert response.status_code == 422, response.text
    assert response.status_code != 500


def test_return_count_rejects_value_beyond_postgres_int4_range(client):
    application = _create_application(client)
    response = _submit(
        client,
        application["id"],
        application["behavior_metrics_capability"],
        return_count=99_999_999_999,
    )
    assert response.status_code == 422, response.text
    assert response.status_code != 500


def test_time_on_page_rejects_negative_value(client):
    application = _create_application(client)
    response = _submit(
        client, application["id"], application["behavior_metrics_capability"], time_on_page=-1
    )
    assert response.status_code == 422


def test_time_on_page_accepts_zero_boundary(client):
    application = _create_application(client)
    response = _submit(
        client, application["id"], application["behavior_metrics_capability"], time_on_page=0
    )
    assert response.status_code == 201, response.text


# --- oversized structures ---------------------------------------------------


def test_clicked_buttons_rejects_oversized_list(client):
    application = _create_application(client)
    response = _submit(
        client,
        application["id"],
        application["behavior_metrics_capability"],
        clicked_buttons=[{"button": f"btn-{i}", "count": 1} for i in range(201)],
    )
    assert response.status_code == 422, response.text
    assert response.status_code != 500


def test_clicked_buttons_accepts_boundary_size_list(client):
    application = _create_application(client)
    response = _submit(
        client,
        application["id"],
        application["behavior_metrics_capability"],
        clicked_buttons=[{"button": f"btn-{i}", "count": 1} for i in range(200)],
    )
    assert response.status_code == 201, response.text


def test_cursor_hover_data_rejects_oversized_dict(client):
    application = _create_application(client)
    response = _submit(
        client,
        application["id"],
        application["behavior_metrics_capability"],
        cursor_hover_data={f"section-{i}": {"hovers": 1, "ms": 1} for i in range(201)},
    )
    assert response.status_code == 422, response.text
    assert response.status_code != 500


def test_clicked_buttons_rejects_oversized_button_name(client):
    application = _create_application(client)
    response = _submit(
        client,
        application["id"],
        application["behavior_metrics_capability"],
        clicked_buttons=[{"button": "x" * 101, "count": 1}],
    )
    assert response.status_code == 422


def test_clicked_buttons_rejects_extra_field_in_entry(client):
    """extra="forbid" on ClickedButtonEntry (see
    app/schemas/behavior_metric.py) rejects an unexpected field outright,
    rather than silently accepting/dropping it - guards against
    accidentally collecting additional data through this collector."""
    application = _create_application(client)
    response = _submit(
        client,
        application["id"],
        application["behavior_metrics_capability"],
        clicked_buttons=[{"button": "cta", "count": 1, "extra_free_text": "unexpected"}],
    )
    assert response.status_code == 422


def test_clicked_buttons_rejects_negative_count(client):
    application = _create_application(client)
    response = _submit(
        client,
        application["id"],
        application["behavior_metrics_capability"],
        clicked_buttons=[{"button": "cta", "count": -1}],
    )
    assert response.status_code == 422


def test_cursor_hover_data_rejects_malformed_entry_shape(client):
    application = _create_application(client)
    response = _submit(
        client,
        application["id"],
        application["behavior_metrics_capability"],
        cursor_hover_data={"hero": "not-a-dict"},
    )
    assert response.status_code == 422


def test_valid_boundary_metrics_payload_succeeds(client):
    application = _create_application(client)
    response = _submit(
        client,
        application["id"],
        application["behavior_metrics_capability"],
        time_on_page=604800,
        return_count=100000,
        clicked_buttons=[{"button": "cta", "count": 1000000}],
        cursor_hover_data={"hero": {"hovers": 1000000, "ms": 2592000000}},
    )
    assert response.status_code == 201, response.text


# --- no 500s for client-controlled validation failures ---------------------


def test_none_of_the_bad_metrics_inputs_ever_500(client):
    application = _create_application(client)
    capability = application["behavior_metrics_capability"]
    bad_bodies = [
        {"time_on_page": 10**15},
        {"return_count": -5},
        {"clicked_buttons": [{"button": "x"}] * 500},
        {"cursor_hover_data": {"a": "not-a-dict"}},
        {"clicked_buttons": "not-a-list"},
    ]
    for overrides in bad_bodies:
        response = _submit(client, application["id"], capability, **overrides)
        assert response.status_code < 500, (overrides, response.status_code, response.text)
        assert "traceback" not in response.text.lower()
