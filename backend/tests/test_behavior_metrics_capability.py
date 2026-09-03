"""Integration tests for the behavior-metrics submission capability against a
real PostgreSQL test database.

Requires TEST_DATABASE_URL, exactly like test_api.py; the whole module is
skipped with an explicit reason if it's unset. These exercise the fix for
the "behavior-metrics first-writer hijacking" finding: POST /behavior-metrics
now requires a one-time capability returned by POST /applications, bound to
exactly one application_id, and single-use (see
app/crud/application_behavior_capability.py).
"""

import threading
from decimal import Decimal

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.crud import application as application_crud
from app.crud import application_behavior_capability as capability_crud
from app.models.application import Application
from app.schemas.application import ApplicationCreate
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


def _submit_metrics(client, application_id, capability, **overrides):
    payload = {"application_id": application_id, "capability": capability}
    payload.update(overrides)
    return client.post("/api/behavior-metrics", json=payload)


# --- creation response shape ------------------------------------------------


def test_application_create_response_includes_a_capability_token(client):
    body = _create_application(client)
    token = body["behavior_metrics_capability"]
    assert isinstance(token, str)
    assert len(token) >= 32  # secrets.token_urlsafe(32) -> 43 chars


def test_two_applications_get_different_capability_tokens(client):
    first = _create_application(client)["behavior_metrics_capability"]
    second = _create_application(client)["behavior_metrics_capability"]
    assert first != second


def test_get_application_never_includes_the_capability(client, admin_auth_headers):
    application_id = _create_application(client)["id"]
    response = client.get(f"/api/applications/{application_id}", headers=admin_auth_headers)
    assert response.status_code == 200
    assert "behavior_metrics_capability" not in response.json()
    assert "capability" not in response.text


def test_list_applications_never_includes_the_capability(client, admin_auth_headers):
    _create_application(client)
    response = client.get("/api/applications", headers=admin_auth_headers)
    assert response.status_code == 200
    assert "behavior_metrics_capability" not in response.text
    assert "capability" not in response.text


# --- guessed / missing / wrong capability -----------------------------------


def test_guessed_application_id_without_capability_fails(client):
    """A missing `capability` field is normalized (schema-level, see
    app/schemas/behavior_metric.py) to the same neutral "invalid capability"
    403 as a wrong one - not a distinct 422, which would otherwise let a
    caller confirm an application_id exists without ever presenting a
    capability at all."""
    application_id = _create_application(client)["id"]
    response = client.post(
        "/api/behavior-metrics", json={"application_id": application_id, "time_on_page": 5}
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Invalid or already-used capability"


def test_wrong_capability_fails(client):
    application_id = _create_application(client)["id"]
    response = _submit_metrics(client, application_id, "not-the-real-token")
    assert response.status_code == 403


def test_empty_capability_fails(client):
    """"" is accepted at the schema level (no min_length) and normalized by
    the route to the same neutral response as any other invalid token."""
    application_id = _create_application(client)["id"]
    response = _submit_metrics(client, application_id, "")
    assert response.status_code == 403
    assert response.json()["detail"] == "Invalid or already-used capability"


def test_non_string_capability_still_gets_ordinary_422(client):
    """The one exception to "every invalid capability -> 403": a
    non-string/object capability fails ordinary schema validation (422)
    before the route ever runs - safe because that rejection depends only
    on the field's JSON type, never on application_id or any DB state (see
    app/schemas/behavior_metric.py)."""
    application_id = _create_application(client)["id"]
    response = client.post(
        "/api/behavior-metrics", json={"application_id": application_id, "capability": 12345}
    )
    assert response.status_code == 422


def test_capability_for_application_a_cannot_submit_metrics_for_application_b(client):
    app_a = _create_application(client)
    app_b = _create_application(client)

    response = _submit_metrics(client, app_b["id"], app_a["behavior_metrics_capability"])
    assert response.status_code == 403

    # And the legitimate capability for B still works afterward - a failed
    # cross-application attempt must not have consumed or damaged it.
    ok = _submit_metrics(client, app_b["id"], app_b["behavior_metrics_capability"])
    assert ok.status_code == 201


def test_capability_does_not_authorize_reading_updating_or_deleting(client):
    """A capability's only power is one POST /behavior-metrics call - it
    must never work as a substitute for an admin bearer token anywhere
    else."""
    application = _create_application(client)
    application_id = application["id"]
    capability = application["behavior_metrics_capability"]

    get_resp = client.get(
        f"/api/applications/{application_id}", headers={"Authorization": f"Bearer {capability}"}
    )
    assert get_resp.status_code == 401

    patch_resp = client.patch(
        f"/api/applications/{application_id}",
        json={"first_name": "Hijacked"},
        headers={"Authorization": f"Bearer {capability}"},
    )
    assert patch_resp.status_code == 401

    delete_resp = client.delete(
        f"/api/applications/{application_id}", headers={"Authorization": f"Bearer {capability}"}
    )
    assert delete_resp.status_code == 401


# --- success and single-use --------------------------------------------------


def test_valid_capability_succeeds_once(client):
    application = _create_application(client)
    response = _submit_metrics(
        client, application["id"], application["behavior_metrics_capability"], time_on_page=42
    )
    assert response.status_code == 201
    assert response.json()["time_on_page"] == 42
    assert "capability" not in response.text


def test_replaying_a_used_capability_fails(client):
    application = _create_application(client)
    application_id = application["id"]
    capability = application["behavior_metrics_capability"]

    first = _submit_metrics(client, application_id, capability)
    assert first.status_code == 201

    replay = _submit_metrics(client, application_id, capability, time_on_page=999)
    # Exactly the same neutral response as any other invalid capability -
    # not merely "some 4xx": there is no longer a separate "already has
    # metrics" (409) response to fall back to (see routes/behavior_metrics.py).
    assert replay.status_code == 403
    assert replay.json()["detail"] == "Invalid or already-used capability"


def _create_application_via_crud(db_engine):
    """Bypasses the shared single-session `client`/`db_session` fixtures on
    purpose - a single SQLAlchemy Session is not safe to use concurrently
    from multiple threads (that fixture is deliberately sequential-only;
    see test_auth_api.py's own race-condition tests for the same
    constraint), and the two tests below need genuinely independent
    concurrent DB connections. Mirrors how those existing tests open
    separate Session(db_engine) connections directly rather than going
    through TestClient/HTTP for the setup step."""
    session = Session(db_engine)
    try:
        application, token = application_crud.create_application(
            session, ApplicationCreate(**_application_payload())
        )
        return application.id, token
    finally:
        session.close()


def _cleanup_applications(db_engine) -> None:
    with Session(db_engine) as session:
        session.execute(delete(Application))  # cascades to metrics + capabilities
        session.commit()


def test_concurrent_submissions_with_the_same_capability_only_one_wins(db_engine):
    """The capability's single-use guarantee must hold even when two
    callers race for the *same still-valid* token - not just when they're
    sequential (test_replaying_a_used_capability_fails above can't exercise
    this: by the time its second request starts, the first has already
    committed a metric row, so the pre-existing duplicate-metric check
    masks the capability check entirely). Exercises
    try_consume_capability() directly (mirrors
    test_auth_api.py::test_register_first_admin_race_condition_only_one_wins)
    so the race is on the capability's own atomic UPDATE, with nothing else
    able to shadow it.
    """
    application_id, token = _create_application_via_crud(db_engine)

    start_barrier = threading.Barrier(2, timeout=10)
    outcomes: list[bool] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def attempt() -> None:
        try:
            start_barrier.wait()
            with Session(db_engine) as session:
                consumed = capability_crud.try_consume_capability(session, application_id, token)
                session.commit()
                with lock:
                    outcomes.append(consumed)
        except BaseException as exc:  # noqa: BLE001 - surfaced via `errors`
            errors.append(exc)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert not any(t.is_alive() for t in threads), (
            "race-condition threads did not finish within the timeout"
        )
        assert not errors, f"unexpected exception(s): {errors!r}"
        assert sorted(outcomes) == [False, True]
    finally:
        _cleanup_applications(db_engine)


def test_concurrent_attacker_without_capability_cannot_beat_the_legitimate_holder(db_engine):
    """An attacker racing a guessed/wrong token against the legitimate
    holder's real token - at the exact same instant - must never win, and
    must never block or corrupt the legitimate consumption."""
    application_id, real_token = _create_application_via_crud(db_engine)

    start_barrier = threading.Barrier(2, timeout=10)
    outcomes: dict[str, bool] = {}
    errors: list[BaseException] = []

    def legitimate() -> None:
        try:
            start_barrier.wait()
            with Session(db_engine) as session:
                consumed = capability_crud.try_consume_capability(
                    session, application_id, real_token
                )
                session.commit()
                outcomes["legitimate"] = consumed
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def attacker() -> None:
        try:
            start_barrier.wait()
            with Session(db_engine) as session:
                consumed = capability_crud.try_consume_capability(
                    session, application_id, "guessed-or-blank-token"
                )
                session.commit()
                outcomes["attacker"] = consumed
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=legitimate), threading.Thread(target=attacker)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert not any(t.is_alive() for t in threads), (
            "race-condition threads did not finish within the timeout"
        )
        assert not errors, f"unexpected exception(s): {errors!r}"
        assert outcomes == {"legitimate": True, "attacker": False}
    finally:
        _cleanup_applications(db_engine)


def test_metrics_remain_unique_per_application_with_capability_flow(client):
    application = _create_application(client)
    application_id = application["id"]

    first = _submit_metrics(client, application_id, application["behavior_metrics_capability"])
    assert first.status_code == 201

    # A brand-new capability can never be manufactured for an application
    # that already has one - there is no HTTP path that issues a second
    # capability for the same application_id (see
    # app.crud.application.create_application, called only once per
    # application, and application_id being UNIQUE on the capabilities
    # table) - so the unique application->metrics invariant is unreachable
    # through the public API, exactly as before this fix.
    second = _submit_metrics(client, application_id, application["behavior_metrics_capability"])
    assert second.status_code == 403


# --- admin analytics/reads unaffected ---------------------------------------


def test_admin_protected_reads_still_work_after_capability_flow(client, admin_auth_headers):
    application = _create_application(client)
    _submit_metrics(client, application["id"], application["behavior_metrics_capability"])

    list_resp = client.get("/api/behavior-metrics", headers=admin_auth_headers)
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1

    overview_resp = client.get("/api/analytics/overview", headers=admin_auth_headers)
    assert overview_resp.status_code == 200
    assert overview_resp.json()["metrics_count"] == 1

    detail_resp = client.get(
        f"/api/analytics/applications/{application['id']}", headers=admin_auth_headers
    )
    assert detail_resp.status_code == 200
    assert detail_resp.json()["has_metrics"] is True


# --- secrets never leak in errors --------------------------------------------


def test_wrong_capability_error_does_not_echo_it_back(client):
    application_id = _create_application(client)["id"]
    secret_guess = "super-secret-guess-token-value"
    response = _submit_metrics(client, application_id, secret_guess)
    assert secret_guess not in response.text


def test_missing_application_error_does_not_echo_the_capability(client):
    secret_guess = "another-secret-token-value"
    response = _submit_metrics(client, 999999, secret_guess)
    assert response.status_code == 403
    assert secret_guess not in response.text


# --- historical data (no capability row at all) -----------------------------


def test_historical_application_without_a_capability_row_fails_neutrally(client, db_session):
    """An Application inserted directly (bypassing
    crud.application.create_application - e.g. data from before this
    table existed, or inserted by a maintenance script) has no matching row
    in application_behavior_capabilities at all. That must be
    indistinguishable from every other invalid-capability reason, not a
    special case."""
    payload = _application_payload()
    payload["budget"] = Decimal(str(payload["budget"]))
    historical_application = Application(**payload)
    db_session.add(historical_application)
    db_session.commit()
    db_session.refresh(historical_application)

    response = _submit_metrics(client, historical_application.id, "any-token-at-all")
    assert response.status_code == 403
    assert response.json()["detail"] == "Invalid or already-used capability"


# --- the single neutral contract, proven across every reason at once --------


def test_every_invalid_capability_reason_yields_the_identical_response(client, db_session):
    """The core Correction 2 property, proven directly: every one of the
    documented reasons a capability could be invalid must produce not just
    "some 4xx" but the exact same status code and exact same JSON body as
    every other one - so an unauthenticated caller without a currently
    valid capability learns nothing that distinguishes them."""
    responses: dict[str, object] = {}

    # nonexistent application + wrong token
    responses["nonexistent_application"] = _submit_metrics(client, 999999, "wrong-token")

    # existing application + wrong token
    app_wrong_token = _create_application(client)
    responses["wrong_token"] = _submit_metrics(client, app_wrong_token["id"], "wrong-token")

    # cross-application token
    app_a = _create_application(client)
    app_b = _create_application(client)
    responses["cross_application_token"] = _submit_metrics(
        client, app_b["id"], app_a["behavior_metrics_capability"]
    )

    # replayed token, and (separately) an existing application that already
    # has metrics but is probed with a *different* wrong token - both must
    # look identical, proving the response never depends on which token was
    # tried once metrics already exist.
    app_replay = _create_application(client)
    first = _submit_metrics(
        client, app_replay["id"], app_replay["behavior_metrics_capability"]
    )
    assert first.status_code == 201
    responses["replayed_token"] = _submit_metrics(
        client, app_replay["id"], app_replay["behavior_metrics_capability"]
    )
    responses["already_has_metrics_wrong_token"] = _submit_metrics(
        client, app_replay["id"], "some-other-wrong-token"
    )

    # historical application, no capability row at all
    payload = _application_payload()
    payload["budget"] = Decimal(str(payload["budget"]))
    historical_application = Application(**payload)
    db_session.add(historical_application)
    db_session.commit()
    db_session.refresh(historical_application)
    responses["historical_no_capability_row"] = _submit_metrics(
        client, historical_application.id, "any-token-at-all"
    )

    # missing capability, normalized by the route
    responses["missing_capability"] = client.post(
        "/api/behavior-metrics",
        json={"application_id": app_wrong_token["id"], "time_on_page": 5},
    )

    statuses = {name: r.status_code for name, r in responses.items()}
    bodies = {name: r.json() for name, r in responses.items()}

    assert set(statuses.values()) == {403}, statuses
    distinct_bodies = {tuple(sorted(body.items())) for body in bodies.values()}
    assert len(distinct_bodies) == 1, bodies
    assert next(iter(bodies.values())) == {"detail": "Invalid or already-used capability"}
