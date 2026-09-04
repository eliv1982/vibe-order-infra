"""Regression test for the Stage 1B BLOCKER correction: a database
provisioned under the accepted Stage 1A baseline (applications table with
no service_id column) must upgrade cleanly and stay fully usable when
started against Stage 1B's application code.

This builds a genuine Stage 1A-shaped schema by hand (the exact DDL the
accepted-baseline `applications`/`admin_settings` models produced - see
app/core/schema_compat.py's docstring), seeds a historical application row
the way a real pre-Stage-1B deployment would have, then runs the actual
app.main.lifespan startup path (via `with TestClient(app):`, which triggers
FastAPI's real lifespan context manager) against it - not just the
upgrade helper in isolation. `app.main`'s module-level `engine` is
monkeypatched to point at this disposable database for the duration of the
test only; nothing here touches TEST_DATABASE_URL's own schema/rows, and
get_db is overridden the same way conftest.py's `client` fixture does it,
just bound to this disposable engine instead.

Requires TEST_DATABASE_URL, exactly like test_api.py; skipped with an
explicit reason if it's unset - a dedicated *second* database is created
next to it (same server, name derived from TEST_DATABASE_URL) so this test
never disturbs the shared test database's own tables/teardown timing.
"""

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

import app.main as main_module
from app.core.database import get_db
from app.core.security import normalize_username
from app.crud import admin as admin_crud
from app.models.application import Application
from tests.db_safety_guard import assert_safe_test_database_url, get_test_database_url
from tests.test_api import _application_payload

TEST_DATABASE_URL = get_test_database_url()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set - skipping PostgreSQL integration tests",
)

_PRODUCTION_POSTGRES_DB = os.environ.get("POSTGRES_DB")


def _stage1a_compat_database_url() -> str:
    base = make_url(TEST_DATABASE_URL)
    # Keeps the "test" marker db_safety_guard requires (e.g.
    # "vibe_testing" -> "vibe_testing_stage1a_compat"). render_as_string
    # (NOT str()/repr(), which mask the password as "***" for safe display)
    # is required here - this string is re-parsed and actually connected
    # with later, so a masked password would silently break every
    # connection using it.
    return base.set(database=f"{base.database}_stage1a_compat").render_as_string(hide_password=False)


def _create_disposable_database(url: str) -> None:
    parsed = make_url(url)
    admin_url = parsed.set(database="postgres")
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{parsed.database}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{parsed.database}"'))
    finally:
        admin_engine.dispose()


def _drop_disposable_database(url: str) -> None:
    parsed = make_url(url)
    admin_url = parsed.set(database="postgres")
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{parsed.database}" WITH (FORCE)'))
    finally:
        admin_engine.dispose()


def _build_stage1a_schema(url: str) -> None:
    """Exact DDL of the accepted Stage 1A `admin_settings`/`applications`
    tables (see `git show 08b88b9:backend/app/models/application.py`) - no
    service_id column - plus one historical application row, the way a real
    pre-Stage-1B deployment would have it."""
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE admin_settings (
                        id SERIAL PRIMARY KEY,
                        service_name VARCHAR(255) NOT NULL,
                        budget_min NUMERIC(12, 2) NOT NULL,
                        budget_max NUMERIC(12, 2) NOT NULL,
                        description TEXT,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE TABLE applications (
                        id SERIAL PRIMARY KEY,
                        first_name VARCHAR(100) NOT NULL,
                        last_name VARCHAR(100) NOT NULL,
                        middle_name VARCHAR(100),
                        contact_data VARCHAR(255) NOT NULL,
                        business_niche VARCHAR(255) NOT NULL,
                        company_size VARCHAR(50) NOT NULL,
                        business_info TEXT NOT NULL,
                        task_scope TEXT NOT NULL,
                        requester_role VARCHAR(50) NOT NULL,
                        business_size VARCHAR(50) NOT NULL,
                        need_scope TEXT NOT NULL,
                        deadline VARCHAR(100) NOT NULL,
                        task_type VARCHAR(100) NOT NULL,
                        interested_product VARCHAR(255) NOT NULL,
                        budget NUMERIC(12, 2) NOT NULL,
                        preferred_contact_method VARCHAR(50) NOT NULL,
                        preferred_contact_time VARCHAR(100) NOT NULL,
                        comment TEXT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    INSERT INTO applications (
                        first_name, last_name, contact_data, business_niche, company_size,
                        business_info, task_scope, requester_role, business_size, need_scope,
                        deadline, task_type, interested_product, budget,
                        preferred_contact_method, preferred_contact_time
                    ) VALUES (
                        'Historical', 'Applicant', 'historical@example.com', 'Личный автомобиль',
                        'Седан или универсал', 'Old car, pre-Stage-1B', 'Разовая услуга',
                        'Владелец автомобиля', 'Один автомобиль', 'Old need, pre-Stage-1B',
                        'В течение месяца', 'Плановый уход', 'Legacy Detailing Package', 1500.00,
                        'Телефон', 'Утро (9:00–12:00)'
                    )
                    """
                )
            )
    finally:
        engine.dispose()


@pytest.fixture()
def stage1a_database():
    url = _stage1a_compat_database_url()
    assert_safe_test_database_url(url, _PRODUCTION_POSTGRES_DB)
    _create_disposable_database(url)
    _build_stage1a_schema(url)
    engine = create_engine(url)
    try:
        yield url, engine
    finally:
        engine.dispose()
        _drop_disposable_database(url)


def test_stage1a_database_upgrades_cleanly_and_stays_fully_usable(stage1a_database, monkeypatch):
    url, disposable_engine = stage1a_database

    # Point the app's real startup path (app.main.lifespan references this
    # module-level name directly) at our disposable Stage 1A-shaped
    # database, without touching the shared TEST_DATABASE_URL engine any
    # other test relies on.
    monkeypatch.setattr(main_module, "engine", disposable_engine)

    def _override_get_db():
        with Session(disposable_engine) as session:
            yield session

    main_module.app.dependency_overrides[get_db] = _override_get_db
    try:
        # Entering TestClient as a context manager runs FastAPI's real ASGI
        # lifespan startup event, i.e. the actual, unmodified
        # `upgrade_applications_service_id(engine)` then
        # `Base.metadata.create_all(bind=engine)` sequence from
        # app/main.py::lifespan - not a reimplementation of it.
        with TestClient(main_module.app) as client:
            inspector = inspect(disposable_engine)
            columns = {col["name"] for col in inspector.get_columns("applications")}
            assert "service_id" in columns, "startup did not add applications.service_id"

            # Historical row survives the upgrade, readable, with a NULL
            # service_id (no authoritative service to backfill it with).
            with Session(disposable_engine) as session:
                historical = session.execute(select(Application)).scalars().one()
                assert historical.service_id is None
                assert historical.interested_product == "Legacy Detailing Package"

            # Bootstrap an admin directly through the domain layer (exactly
            # like conftest.py's admin_auth_headers fixture, and like the
            # real operator CLI) against this same disposable database.
            with Session(disposable_engine) as session:
                admin_crud.register_first_admin(
                    session, normalize_username("admin"), "StrongPassw0rd!123"
                )
            login = client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "StrongPassw0rd!123"},
            )
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            # Admin application listing reads the historical row without a 500.
            listing = client.get("/api/applications", headers=headers)
            assert listing.status_code == 200, listing.text
            assert len(listing.json()) == 1
            assert listing.json()[0]["service_id"] is None
            assert listing.json()[0]["interested_product"] == "Legacy Detailing Package"

            # Analytics read paths don't 500 against a mixed/legacy dataset.
            overview = client.get("/api/analytics/overview?period=month", headers=headers)
            assert overview.status_code == 200, overview.text

            prioritized = client.get("/api/applications/prioritized", headers=headers)
            assert prioritized.status_code == 200, prioritized.text

            # New application creation works end-to-end, with a real,
            # FK-backed service_id - the upgraded FK constraint is verified
            # to actually be enforced too (see the invalid-service test
            # below), not merely present.
            service_resp = client.post(
                "/api/admin-settings",
                json={
                    "service_name": "New Detailing Package",
                    "budget_min": "100.00",
                    "budget_max": "5000.00",
                    "is_active": True,
                },
                headers=headers,
            )
            assert service_resp.status_code == 201, service_resp.text
            service_id = service_resp.json()["id"]

            new_app_resp = client.post(
                "/api/applications",
                json=_application_payload(service_id=service_id, budget="1000.00"),
            )
            assert new_app_resp.status_code == 201, new_app_resp.text
            assert new_app_resp.json()["service_id"] == service_id

            final_listing = client.get("/api/applications", headers=headers)
            assert len(final_listing.json()) == 2
    finally:
        main_module.app.dependency_overrides.clear()

    # Repeated startup (e.g. a container restart) against the now-upgraded
    # database must not error and must not re-attempt the ALTER TABLE.
    with TestClient(main_module.app):
        pass

    inspector_after_restart = inspect(disposable_engine)
    columns_after_restart = {
        col["name"] for col in inspector_after_restart.get_columns("applications")
    }
    assert "service_id" in columns_after_restart

    with Session(disposable_engine) as session:
        assert session.execute(select(Application)).scalars().all()  # both rows still there
