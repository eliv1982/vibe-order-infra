"""Integration tests for idempotent POST /applications via the
Idempotency-Key header (Stage 1B, section D) against a real PostgreSQL test
database.

Requires TEST_DATABASE_URL, exactly like test_api.py; the whole module is
skipped with an explicit reason if it's unset. See app/crud/application.py's
module-level design note for the full behavior contract this proves.
"""

import secrets
import threading

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_capability_token, hash_idempotency_key
from app.crud import application as application_crud
from app.models.application import Application
from app.models.application_behavior_capability import ApplicationBehaviorCapability
from app.models.application_idempotency_key import ApplicationIdempotencyKey
from app.models.behavior_metric import BehaviorMetric
from app.schemas.application import ApplicationCreate
from tests.db_safety_guard import get_test_database_url
from tests.test_api import _application_payload

TEST_DATABASE_URL = get_test_database_url()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set - skipping PostgreSQL integration tests",
)


def _new_key() -> str:
    # Matches the frontend's generateIdempotencyKey() shape closely enough
    # for testing the format/entropy bound (see routes/applications.py's
    # _IDEMPOTENCY_KEY_RE, which requires >=43 characters) without depending
    # on a specific generator - secrets.token_urlsafe(32) produces exactly
    # 43 characters, same as frontend/src/utils/idempotencyKey.ts.
    return secrets.token_urlsafe(32)


def _post_application(client, key: str | None, **overrides):
    headers = {"Idempotency-Key": key} if key is not None else {}
    return client.post("/api/applications", json=_application_payload(**overrides), headers=headers)


# --- sequential retry: same key + same payload -----------------------------


def test_sequential_retry_same_key_same_payload_returns_original_application(client):
    key = _new_key()
    first = _post_application(client, key)
    assert first.status_code == 201, first.text

    retry = _post_application(client, key)
    assert retry.status_code == 201, retry.text
    assert retry.json()["id"] == first.json()["id"]
    assert retry.json()["created_at"] == first.json()["created_at"]


def test_sequential_retry_does_not_create_a_second_application(client, admin_auth_headers):
    key = _new_key()
    _post_application(client, key)
    _post_application(client, key)
    _post_application(client, key)

    listing = client.get("/api/applications", headers=admin_auth_headers)
    assert listing.status_code == 200
    assert len(listing.json()) == 1


def test_response_loss_retry_simulation(client, admin_auth_headers):
    """Simulates the exact failure this feature targets: the server
    committed the first request but the client never saw the response (e.g.
    a dropped connection) - the client's only recourse is to retry with the
    same key. That retry must be safe and return the same logical result.

    This is the intended, secure Stage 1B contract: the retry recovers the
    original Application (no duplicate is ever created), but it does NOT
    recover the lost capability - that response is gone for good, by
    design, since a caller-chosen Idempotency-Key must never double as
    proof of possession of the original request (see
    app/crud/application.py::create_application_idempotent's module-level
    design note)."""
    key = _new_key()
    lost_response = _post_application(client, key)
    assert lost_response.status_code == 201
    original_id = lost_response.json()["id"]

    # The client, unaware the first attempt succeeded, retries.
    retry = _post_application(client, key)
    assert retry.status_code == 201
    assert retry.json()["id"] == original_id
    assert retry.json()["behavior_metrics_capability"] is None

    listing = client.get("/api/applications", headers=admin_auth_headers)
    assert len(listing.json()) == 1


# --- same key + different payload -> deterministic rejection --------------


def test_same_key_different_payload_is_rejected(client):
    key = _new_key()
    first = _post_application(client, key, first_name="Ivan")
    assert first.status_code == 201

    conflicting = _post_application(client, key, first_name="Petr")
    assert conflicting.status_code == 409
    assert "id" not in conflicting.json()  # a conflict body, never an application body


def test_same_key_different_payload_does_not_leak_the_original_payload(client):
    key = _new_key()
    _post_application(client, key, first_name="SecretOriginalName")
    conflicting = _post_application(client, key, first_name="DifferentName")
    assert conflicting.status_code == 409
    assert "SecretOriginalName" not in conflicting.text


def test_same_key_different_payload_does_not_create_a_second_application(
    client, admin_auth_headers
):
    key = _new_key()
    _post_application(client, key, first_name="Ivan")
    _post_application(client, key, first_name="Petr")

    listing = client.get("/api/applications", headers=admin_auth_headers)
    assert len(listing.json()) == 1


# --- distinct keys + same payload -> distinct applications -----------------


def test_distinct_keys_same_payload_create_distinct_applications(client):
    first = _post_application(client, _new_key())
    second = _post_application(client, _new_key())
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


# --- missing header: unchanged, non-idempotent behavior --------------------


def test_missing_idempotency_key_creates_a_new_application_every_time(client):
    """Backward compatible / opt-in: a caller that never sends the header
    (e.g. any client written before Stage 1B) gets exactly the old
    behavior."""
    first = _post_application(client, None)
    second = _post_application(client, None)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


# --- malformed key format ---------------------------------------------------


@pytest.mark.parametrize(
    "bad_key",
    [
        "short",  # below 16 chars
        "x" * 42,  # one under the new 43-char entropy floor
        secrets.token_urlsafe(24),  # 32 chars - the *old* Stage 1B minimum, now too weak
        "x" * 129,  # above 128 chars
        "has a space in it!!",
        "has/slash+plus=equals",
        "",
    ],
)
def test_malformed_idempotency_key_is_rejected(client, bad_key):
    response = client.post(
        "/api/applications",
        json=_application_payload(),
        headers={"Idempotency-Key": bad_key},
    )
    assert response.status_code == 422


def test_boundary_length_idempotency_keys_are_accepted(client):
    for length in (43, 128):
        key = secrets.token_urlsafe(96)[:length].ljust(length, "a")
        response = _post_application(client, key)
        assert response.status_code == 201, (length, response.text)


# --- idempotency key secrecy: digest-only storage (Stage 1B correction, section D) ---


def test_raw_idempotency_key_is_never_persisted(client, db_session):
    """The literal Idempotency-Key header value must not appear anywhere in
    the persisted row - only a SHA-256 digest of it (see
    app/models/application_idempotency_key.py)."""
    key = _new_key()
    _post_application(client, key)

    row = db_session.scalars(select(ApplicationIdempotencyKey)).one()
    assert row.idempotency_key_hash != key
    assert key not in row.idempotency_key_hash
    # No plaintext idempotency_key column exists at all any more.
    assert not hasattr(row, "idempotency_key")


def test_db_stores_exactly_the_expected_sha256_digest(client, db_session):
    key = _new_key()
    _post_application(client, key)

    row = db_session.scalars(select(ApplicationIdempotencyKey)).one()
    assert row.idempotency_key_hash == hash_idempotency_key(key)
    assert len(row.idempotency_key_hash) == 64  # sha256 hex digest length


def test_submitting_the_stored_digest_as_a_header_does_not_replay_the_original(
    client, db_session, admin_auth_headers
):
    """Proves the digest cannot be used as a working idempotency key: reading
    it out of the database (e.g. via a DB-level compromise) must not hand
    over anything that can recover or replay the original request."""
    key = _new_key()
    original = _post_application(client, key)
    assert original.status_code == 201
    original_id = original.json()["id"]

    row = db_session.scalars(select(ApplicationIdempotencyKey)).one()
    stolen_digest = row.idempotency_key_hash
    assert len(stolen_digest) == 64  # passes the 43-128 char format check

    replay_attempt = _post_application(client, stolen_digest)
    # The digest hashes to something else entirely, so this is treated as a
    # brand-new, never-before-seen key - it creates its own application
    # rather than returning/leaking the original one.
    assert replay_attempt.status_code == 201
    assert replay_attempt.json()["id"] != original_id

    listing = client.get("/api/applications", headers=admin_auth_headers)
    assert len(listing.json()) == 2


def test_strong_valid_key_still_works_end_to_end(client):
    key = secrets.token_urlsafe(32)
    first = _post_application(client, key)
    assert first.status_code == 201, first.text
    retry = _post_application(client, key)
    assert retry.status_code == 201
    assert retry.json()["id"] == first.json()["id"]


# --- decimal budget canonicalization (Stage 1B correction, section C) ------


@pytest.mark.parametrize("budget_repr", [1000, 1000.0, "1000.00", "1e3"])
def test_equivalent_decimal_budget_representations_share_idempotency_key(
    client, admin_auth_headers, budget_repr
):
    """Each representation below is mathematically the same accepted budget
    (1000.00) - a client retrying with a re-serialized copy of the same
    logical request must never see a spurious 409 or create a second
    application, regardless of which of these four shapes it happens to
    send this time."""
    key = _new_key()
    first = client.post(
        "/api/applications",
        json=_application_payload(budget=1000),
        headers={"Idempotency-Key": key},
    )
    assert first.status_code == 201, first.text

    retry = client.post(
        "/api/applications",
        json=_application_payload(budget=budget_repr),
        headers={"Idempotency-Key": key},
    )
    assert retry.status_code == 201, (budget_repr, retry.text)
    assert retry.json()["id"] == first.json()["id"]

    listing = client.get("/api/applications", headers=admin_auth_headers)
    assert len(listing.json()) == 1


def test_all_four_equivalent_budget_encodings_together_still_create_one_application(
    client, admin_auth_headers, db_session
):
    key = _new_key()
    encodings = [1000, 1000.0, "1000.00", "1e3"]
    responses = [
        client.post(
            "/api/applications",
            json=_application_payload(budget=encoding),
            headers={"Idempotency-Key": key},
        )
        for encoding in encodings
    ]
    for response in responses:
        assert response.status_code == 201, (response.text, [r.text for r in responses])
        assert response.status_code != 409

    ids = {response.json()["id"] for response in responses}
    assert len(ids) == 1

    listing = client.get("/api/applications", headers=admin_auth_headers)
    assert len(listing.json()) == 1

    mapping_rows = db_session.scalars(
        select(ApplicationIdempotencyKey).where(
            ApplicationIdempotencyKey.idempotency_key_hash == hash_idempotency_key(key)
        )
    ).all()
    assert len(mapping_rows) == 1


def test_genuinely_different_budget_under_same_key_still_conflicts(client):
    """Canonicalization must never blur two *actually different* budgets
    together - only representations of the same amount collapse."""
    key = _new_key()
    first = client.post(
        "/api/applications",
        json=_application_payload(budget=1000),
        headers={"Idempotency-Key": key},
    )
    assert first.status_code == 201

    conflicting = client.post(
        "/api/applications",
        json=_application_payload(budget=1000.01),
        headers={"Idempotency-Key": key},
    )
    assert conflicting.status_code == 409


# --- capability behavior on retry (Stage 1B correction) --------------------
#
# A replay must never be treated as authorization to mint or recover a
# behavior-metrics capability - the Idempotency-Key is caller-chosen and its
# format/length constraints bound collisions, not entropy (see
# app/routes/applications.py). Every one of the tests below therefore
# expects behavior_metrics_capability: null on replay, unconditionally - see
# app/crud/application.py::create_application_idempotent's module-level
# design note for the full rationale.


def test_idempotent_retry_returns_null_capability_while_original_still_unused(client):
    key = _new_key()
    first = _post_application(client, key)
    assert first.status_code == 201
    application_id = first.json()["id"]
    original_capability = first.json()["behavior_metrics_capability"]
    assert original_capability is not None

    retry = _post_application(client, key)
    assert retry.status_code == 201
    assert retry.json()["behavior_metrics_capability"] is None
    assert retry.json()["id"] == application_id

    # The replay changed nothing: the original, still-unconsumed capability
    # keeps working exactly as if no replay had ever happened.
    metrics_resp = client.post(
        "/api/behavior-metrics",
        json={"application_id": application_id, "capability": original_capability, "time_on_page": 5},
    )
    assert metrics_resp.status_code == 201, metrics_resp.text


def test_original_capability_still_works_after_an_idempotent_replay(client, db_session):
    """A replay must not rotate the still-unconsumed capability - the same
    digest that existed before the replay must still be there after it, and
    must still authorize exactly one metrics submission."""
    key = _new_key()
    first = _post_application(client, key)
    application_id = first.json()["id"]
    original_capability = first.json()["behavior_metrics_capability"]

    row_before = db_session.scalars(
        select(ApplicationBehaviorCapability).where(
            ApplicationBehaviorCapability.application_id == application_id
        )
    ).one()
    digest_before = row_before.capability_hash

    retry = _post_application(client, key)
    assert retry.status_code == 201
    assert retry.json()["behavior_metrics_capability"] is None

    db_session.expire_all()
    row_after = db_session.scalars(
        select(ApplicationBehaviorCapability).where(
            ApplicationBehaviorCapability.application_id == application_id
        )
    ).one()
    assert row_after.capability_hash == digest_before, "replay must not rotate the capability digest"

    fresh_attempt = client.post(
        "/api/behavior-metrics",
        json={"application_id": application_id, "capability": original_capability, "time_on_page": 5},
    )
    assert fresh_attempt.status_code == 201


def test_retry_after_capability_already_consumed_returns_null_capability(client, db_session):
    """Once metrics were already submitted under the first response, a
    retry cannot fabricate a second valid capability - see
    ApplicationCreateRead's docstring."""
    key = _new_key()
    first = _post_application(client, key)
    application_id = first.json()["id"]
    capability = first.json()["behavior_metrics_capability"]

    consume_resp = client.post(
        "/api/behavior-metrics",
        json={"application_id": application_id, "capability": capability, "time_on_page": 5},
    )
    assert consume_resp.status_code == 201
    metrics_id = consume_resp.json()["id"]

    retry = _post_application(client, key)
    assert retry.status_code == 201
    assert retry.json()["behavior_metrics_capability"] is None

    # No new capability row/digest was created for this replay, and the
    # already-submitted metric was never touched (still exactly one row,
    # with its original id and value).
    capability_rows = db_session.scalars(
        select(ApplicationBehaviorCapability).where(
            ApplicationBehaviorCapability.application_id == application_id
        )
    ).all()
    assert len(capability_rows) == 1

    metric_rows = db_session.scalars(
        select(BehaviorMetric).where(BehaviorMetric.application_id == application_id)
    ).all()
    assert len(metric_rows) == 1
    assert metric_rows[0].id == metrics_id
    assert metric_rows[0].time_on_page == 5

    # null cannot be used to authorize a submission either - it folds into
    # the same neutral "invalid capability" outcome as any other bad value.
    replay_capability_attempt = client.post(
        "/api/behavior-metrics",
        json={"application_id": application_id, "capability": None, "time_on_page": 999},
    )
    assert replay_capability_attempt.status_code == 403


def test_no_duplicate_capability_rows_after_multiple_retries(client, db_session):
    key = _new_key()
    application_id = _post_application(client, key).json()["id"]
    _post_application(client, key)
    _post_application(client, key)

    rows = db_session.scalars(
        select(ApplicationBehaviorCapability).where(
            ApplicationBehaviorCapability.application_id == application_id
        )
    ).all()
    assert len(rows) == 1


def test_capability_plaintext_still_never_stored_across_retries(client, db_session):
    key = _new_key()
    first = _post_application(client, key)
    retry = _post_application(client, key)
    application_id = first.json()["id"]

    assert retry.json()["behavior_metrics_capability"] is None

    row = db_session.scalars(
        select(ApplicationBehaviorCapability).where(
            ApplicationBehaviorCapability.application_id == application_id
        )
    ).one()
    assert first.json()["behavior_metrics_capability"] not in row.capability_hash
    assert len(row.capability_hash) == 64  # sha256 hex digest length


# --- weak/predictable key: format-valid is not the same as unguessable -----


def test_predictable_key_still_only_grants_the_original_capability_once(
    client, admin_auth_headers, db_session
):
    """Proves the core Stage 1B correction directly with the exact
    predictable key an attacker could trivially guess: even though it
    satisfies the syntactic Idempotency-Key contract (43-128 chars from
    [A-Za-z0-9_-]), it authorizes nothing beyond duplicate-request
    recognition - it can never be used to mint or recover a capability."""
    key = "A" * 43
    assert len(key) == 43

    first = _post_application(client, key)
    assert first.status_code == 201, first.text
    application_id = first.json()["id"]
    original_capability = first.json()["behavior_metrics_capability"]
    assert original_capability is not None

    replay = _post_application(client, key)
    assert replay.status_code == 201
    assert replay.json()["id"] == application_id
    assert replay.json()["behavior_metrics_capability"] is None

    # Exactly one application, exactly one idempotency mapping.
    listing = client.get("/api/applications", headers=admin_auth_headers)
    assert len(listing.json()) == 1
    mapping_rows = db_session.scalars(
        select(ApplicationIdempotencyKey).where(
            ApplicationIdempotencyKey.idempotency_key_hash == hash_idempotency_key(key)
        )
    ).all()
    assert len(mapping_rows) == 1

    # The stored capability digest is unchanged - the replay did not rotate
    # it - and the original capability (the only one ever handed out) still
    # authorizes exactly one submission.
    capability_row = db_session.scalars(
        select(ApplicationBehaviorCapability).where(
            ApplicationBehaviorCapability.application_id == application_id
        )
    ).one()
    assert capability_row.capability_hash == hash_capability_token(original_capability)

    metrics_resp = client.post(
        "/api/behavior-metrics",
        json={"application_id": application_id, "capability": original_capability, "time_on_page": 5},
    )
    assert metrics_resp.status_code == 201, metrics_resp.text

    # Once more, now that the capability is consumed: still no second one.
    second_replay = _post_application(client, key)
    assert second_replay.status_code == 201
    assert second_replay.json()["behavior_metrics_capability"] is None


# --- concurrency -------------------------------------------------------


def _cleanup(db_engine) -> None:
    from sqlalchemy import delete

    with Session(db_engine) as session:
        session.execute(delete(Application))  # cascades to metrics/capabilities/idempotency keys
        session.commit()


def test_concurrent_retry_same_key_same_payload_creates_exactly_one_application(db_engine):
    """Two callers racing with the *same* Idempotency-Key and payload -
    neither has seen a response yet - must still result in exactly one
    Application, and at most one of them may ever receive a real capability
    (Stage 1B correction: a replay caller must never receive a newly minted
    capability - only the single winning first-creation response may
    contain one). Mirrors test_behavior_metrics_capability.py's own
    concurrency tests: a single shared Session isn't safe across threads,
    so each thread opens its own Session(db_engine) directly and calls the
    crud function rather than going through TestClient."""
    key = _new_key()
    payload = ApplicationCreate(**_application_payload())

    outcomes: list[int] = []
    tokens: list[str | None] = []
    errors: list[BaseException] = []
    lock = threading.Lock()
    start_barrier = threading.Barrier(2, timeout=10)

    def attempt() -> None:
        try:
            start_barrier.wait()
            with Session(db_engine) as session:
                application, token = application_crud.create_application_idempotent(
                    session, payload, key
                )
                with lock:
                    outcomes.append(application.id)
                    tokens.append(token)
        except BaseException as exc:  # noqa: BLE001 - surfaced via `errors`
            errors.append(exc)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert not any(t.is_alive() for t in threads), "race threads did not finish in time"
        assert not errors, f"unexpected exception(s): {errors!r}"
        assert len(outcomes) == 2
        assert outcomes[0] == outcomes[1], "both concurrent callers must resolve to the same application"
        assert sorted(tok is None for tok in tokens) == [False, True], (
            "exactly one of the two racers is the winning first-creation call and gets a "
            "real capability - the other (the replay) must get None"
        )

        with Session(db_engine) as session:
            count = session.scalar(
                select(Application.id).where(Application.id == outcomes[0])
            )
            assert count == outcomes[0]
            all_ids = session.scalars(select(Application.id)).all()
            assert len(all_ids) == 1

            capability_rows = session.scalars(
                select(ApplicationBehaviorCapability).where(
                    ApplicationBehaviorCapability.application_id == outcomes[0]
                )
            ).all()
            assert len(capability_rows) == 1
    finally:
        _cleanup(db_engine)
