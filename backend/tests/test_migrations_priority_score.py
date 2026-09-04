"""Stage 4 correction (minor): permanent, automated coverage for
applications.priority_score across its three write paths -

1. the one-time 0004 migration backfill of pre-existing (revision 0003)
   rows (backend/alembic/versions/0004_stage4_priority_score.py);
2. a brand-new row inserted via the ORM on the current (head) schema;
3. an existing row whose score-affecting fields change through a supported
   repository write path (app/crud/application.py::update_application).

The independent Stage 4 audit already verified this behavior manually; this
file makes it permanent. Every expected score/level below is computed by
calling app.services.application_scoring.score_application() itself -
never hand-duplicated - so this file can never silently drift from the one
true implementation of the scoring rules.

Requires TEST_DATABASE_URL with CREATEDB+CREATEROLE rights, exactly like
tests/test_migrations_fresh_install.py - see tests/stage2_db_helpers.py's
module docstring. Never calls Base.metadata.create_all() - schema state
comes solely from the real `alembic upgrade` command, at the specific
revisions each test needs.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.crud.application import update_application
from app.db_admin.bootstrap_roles import bootstrap_roles
from app.models.application import Application
from app.schemas.application import ApplicationUpdate
from app.services.application_scoring import ApplicationScore, score_application
from tests import stage2_db_helpers as h
from tests.test_api import _application_payload
from tests.test_application_prioritization_api import _SCORE_PROFILES

pytestmark = pytest.mark.skipif(
    not h.TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set - skipping PostgreSQL integration tests",
)

# low, medium, hot - see this project's standalone verification that these
# three _SCORE_PROFILES entries land in three different bands (15/low,
# 54/medium, 100/hot) under the current scoring rules.
_HISTORICAL_PROFILES = (_SCORE_PROFILES[0], _SCORE_PROFILES[2], _SCORE_PROFILES[4])


@pytest.fixture()
def stage2_database():
    name = h.disposable_database_name("priority_score")
    h.create_disposable_database(name)
    bootstrap_roles(h.role_bootstrap_config(name))
    try:
        yield h.Stage2Database(
            name=name, migration_url=h.migration_database_url(name), app_url=h.app_database_url(name)
        )
    finally:
        h.drop_disposable_database(name)


def _scorable_payload(first_name: str, profile: dict, interested_product: str) -> dict:
    """A full ScorableApplication-shaped dict (see app/services/
    application_scoring.py's Protocol) for `profile`, built from the same
    canonical payload builder every other integration test uses - never a
    second, hand-written set of field values that could drift from it."""
    payload = _application_payload(first_name=first_name, **profile)
    payload["budget"] = Decimal(str(payload["budget"]))
    payload["interested_product"] = interested_product
    payload.pop("service_id", None)
    payload["comment"] = None
    return payload


def _expected_score(first_name: str, profile: dict, interested_product: str) -> ApplicationScore:
    return score_application(SimpleNamespace(**_scorable_payload(first_name, profile, interested_product)))


def _insert_revision_0003_application_rows(engine, profiles: tuple[dict, ...]) -> list[int]:
    """Raw SQL INSERT against the Stage 1B (revision 0003) `applications`
    schema, which has no priority_score column yet - deliberately not using
    the Application ORM model, since that model already maps priority_score
    (see app/models/application.py) and would try to write a column this
    schema revision doesn't have."""
    ids: list[int] = []
    with engine.begin() as conn:
        for i, profile in enumerate(profiles):
            payload = _scorable_payload(f"Legacy{i}", profile, "Legacy Service")
            row_id = conn.execute(
                text(
                    "INSERT INTO applications ("
                    "first_name, last_name, contact_data, business_niche, company_size, "
                    "business_info, task_scope, requester_role, business_size, need_scope, "
                    "deadline, task_type, interested_product, budget, "
                    "preferred_contact_method, preferred_contact_time"
                    ") VALUES ("
                    ":first_name, :last_name, :contact_data, :business_niche, :company_size, "
                    ":business_info, :task_scope, :requester_role, :business_size, :need_scope, "
                    ":deadline, :task_type, :interested_product, :budget, "
                    ":preferred_contact_method, :preferred_contact_time"
                    ") RETURNING id"
                ),
                {key: payload[key] for key in payload if key != "comment"},
            ).scalar_one()
            ids.append(row_id)
    return ids


def test_backfill_matches_score_application_across_several_bands(stage2_database):
    cfg = h.alembic_config_for(stage2_database.migration_url)
    command.upgrade(cfg, "0003_stage1b_service_idemp")

    engine = create_engine(stage2_database.migration_url)
    try:
        ids = _insert_revision_0003_application_rows(engine, _HISTORICAL_PROFILES)

        # The migration under test: backfills priority_score for the rows
        # just inserted, then adds the NOT NULL/indexed column for real (see
        # backend/alembic/versions/0004_stage4_priority_score.py).
        command.upgrade(cfg, "head")

        with engine.connect() as conn:
            stored = dict(
                conn.execute(
                    text("SELECT id, priority_score FROM applications WHERE id = ANY(:ids)"),
                    {"ids": ids},
                ).all()
            )
    finally:
        engine.dispose()

    expected_levels = set()
    for i, (row_id, profile) in enumerate(zip(ids, _HISTORICAL_PROFILES)):
        expected = _expected_score(f"Legacy{i}", profile, "Legacy Service")
        assert stored[row_id] == expected.score, (
            f"row {row_id} (profile {i}): backfilled priority_score does not match "
            "score_application() for the exact same fields"
        )
        expected_levels.add(expected.level)

    # Covers several score bands, not just "the backfill ran without error".
    assert expected_levels == {"low", "medium", "hot"}


def test_fresh_application_via_orm_gets_expected_priority_score(stage2_database):
    cfg = h.alembic_config_for(stage2_database.migration_url)
    command.upgrade(cfg, "head")
    # Mirrors docker-compose.yml's db-roles-finalize step (see
    # test_migrations_fresh_install.py::test_backend_starts_and_operates_with_runtime_role) -
    # catches the tables/sequences the migration just created.
    bootstrap_roles(h.role_bootstrap_config(stage2_database.name))

    payload = _scorable_payload("FreshHot", _SCORE_PROFILES[4], "Fresh Service")
    expected = score_application(SimpleNamespace(**payload))

    engine = create_engine(stage2_database.app_url)
    try:
        with Session(engine) as session:
            application = Application(**payload)
            session.add(application)
            session.commit()
            session.refresh(application)
            stored_score = application.priority_score
    finally:
        engine.dispose()

    assert stored_score == expected.score
    assert expected.level == "hot"


def test_updating_a_score_affecting_field_resyncs_priority_score(stage2_database):
    cfg = h.alembic_config_for(stage2_database.migration_url)
    command.upgrade(cfg, "head")
    bootstrap_roles(h.role_bootstrap_config(stage2_database.name))

    initial_payload = _scorable_payload("UpdateMe", _SCORE_PROFILES[0], "Update Service")
    initial_expected = score_application(SimpleNamespace(**initial_payload))

    hot_overrides = _SCORE_PROFILES[4]
    updated_payload = dict(initial_payload)
    updated_payload.update(hot_overrides)
    # _SCORE_PROFILES stores budget as a plain string (see
    # test_application_prioritization_api.py) - _scorable_payload already
    # normalized initial_payload's budget to Decimal, but the raw override
    # dict just replaced it with that string again.
    updated_payload["budget"] = Decimal(str(updated_payload["budget"]))
    updated_expected = score_application(SimpleNamespace(**updated_payload))

    engine = create_engine(stage2_database.app_url)
    try:
        with Session(engine) as session:
            application = Application(**initial_payload)
            session.add(application)
            session.commit()
            session.refresh(application)
            application_id = application.id
            initial_stored_score = application.priority_score

        with Session(engine) as session:
            updated = update_application(
                session,
                application_id,
                ApplicationUpdate(**hot_overrides),
            )
            assert updated is not None
            new_stored_score = updated.priority_score
    finally:
        engine.dispose()

    assert initial_stored_score == initial_expected.score
    assert initial_expected.level == "low"
    assert new_stored_score == updated_expected.score
    assert updated_expected.level == "hot"
    assert new_stored_score != initial_stored_score
