"""Stage 2, mandatory production-like upgrade proof (see the Stage 2 spec,
section 12) - the highest-value Stage 2 test.

Builds a disposable PostgreSQL database with the exact pre-Stage-1A
production schema (verified against `git show e93238b:backend/app/models/`
- see backend/alembic/versions/0001_legacy_baseline.py's docstring),
seeds representative historical rows across all four legacy categories
(admins, admin_settings, applications, behavior_metrics), then runs the
*real* adoption + migration flow end to end:

    app.db_admin.bootstrap_roles.bootstrap_roles()
    -> app.db_admin.adopt_legacy.adopt_legacy_database()
    -> alembic upgrade head (Alembic's own command API)
    -> app.db_admin.bootstrap_roles.bootstrap_roles() again (finalize grants)

Never calls Base.metadata.create_all() anywhere in this file.

Requires TEST_DATABASE_URL with CREATEDB+CREATEROLE rights - see
tests/stage2_db_helpers.py's module docstring.
"""

from decimal import Decimal

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

import app.main as main_module
from app.core.database import get_db
from app.core.security import hash_password
from app.db_admin.adopt_legacy import LegacySchemaVerificationError, adopt_legacy_database
from app.db_admin.bootstrap_roles import bootstrap_roles
from tests import stage2_db_helpers as h

pytestmark = pytest.mark.skipif(
    not h.TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set - skipping PostgreSQL integration tests",
)

_LEGACY_ADMIN_USERNAME = "legacyadmin"
_LEGACY_ADMIN_PASSWORD = "LegacyAdminPassw0rd!123"

_LEGACY_SCHEMA_DDL = """
CREATE TABLE admins (
    id SERIAL PRIMARY KEY,
    username VARCHAR(150) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    is_active BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE admin_settings (
    id SERIAL PRIMARY KEY,
    service_name VARCHAR(255) NOT NULL,
    budget_min NUMERIC(12, 2) NOT NULL,
    budget_max NUMERIC(12, 2) NOT NULL,
    description TEXT,
    is_active BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

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
);

CREATE TABLE behavior_metrics (
    id SERIAL PRIMARY KEY,
    application_id INTEGER NOT NULL UNIQUE REFERENCES applications(id) ON DELETE CASCADE,
    time_on_page INTEGER NOT NULL,
    clicked_buttons JSONB NOT NULL,
    cursor_hover_data JSONB NOT NULL,
    return_count INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def _seed_legacy_data(engine, admin_password_hash: str) -> None:
    with engine.begin() as conn:
        conn.execute(text(_LEGACY_SCHEMA_DDL))
        conn.execute(
            text(
                "INSERT INTO admins (username, password_hash, is_active) "
                "VALUES (:username, :password_hash, true)"
            ),
            {"username": _LEGACY_ADMIN_USERNAME, "password_hash": admin_password_hash},
        )
        conn.execute(
            text(
                "INSERT INTO admin_settings (service_name, budget_min, budget_max, is_active) "
                "VALUES ('Legacy Detailing Package', 500.00, 5000.00, true)"
            )
        )
        for i in range(2):
            conn.execute(
                text(
                    "INSERT INTO applications ("
                    "first_name, last_name, contact_data, business_niche, company_size, "
                    "business_info, task_scope, requester_role, business_size, need_scope, "
                    "deadline, task_type, interested_product, budget, "
                    "preferred_contact_method, preferred_contact_time"
                    ") VALUES ("
                    f"'Historical{i}', 'Applicant', 'historical{i}@example.com', 'Личный автомобиль', "
                    "'Седан или универсал', 'Old car, pre-Stage-1B', 'Разовая услуга', "
                    "'Владелец автомобиля', 'Один автомобиль', 'Old need, pre-Stage-1B', "
                    "'В течение месяца', 'Плановый уход', 'Legacy Detailing Package', "
                    f"{1500 + i * 100}.00, 'Телефон', 'Утро (9:00-12:00)'"
                    ")"
                )
            )
        conn.execute(
            text(
                "INSERT INTO behavior_metrics (application_id, time_on_page, clicked_buttons, "
                "cursor_hover_data, return_count) "
                "VALUES (1, 120, '[\"submit\"]'::jsonb, '{\"x\": 1}'::jsonb, 2)"
            )
        )


@pytest.fixture()
def legacy_stage2_database():
    name = h.disposable_database_name("legacy")
    h.create_disposable_database(name)
    admin_url = h.admin_url().set(database=name)
    seeding_engine = create_engine(admin_url)
    try:
        _seed_legacy_data(seeding_engine, hash_password(_LEGACY_ADMIN_PASSWORD))
    finally:
        seeding_engine.dispose()

    config = h.role_bootstrap_config(name)
    bootstrap_roles(config)  # roles + default privileges, before adoption/migration

    try:
        yield h.Stage2Database(
            name=name, migration_url=h.migration_database_url(name), app_url=h.app_database_url(name)
        )
    finally:
        h.drop_disposable_database(name)


def test_legacy_database_adoption_and_upgrade_end_to_end(legacy_stage2_database):
    db = legacy_stage2_database

    # 1 & 2. Legacy schema recognized correctly; baseline stamp/adoption succeeds.
    message = adopt_legacy_database(h.adoption_config(db.name))
    assert "adopted" in message.lower()

    admin_engine = create_engine(h.admin_url().set(database=db.name))
    try:
        with admin_engine.connect() as conn:
            stamped_version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert stamped_version == "0001_legacy_baseline"
    finally:
        admin_engine.dispose()

    # 3. `alembic upgrade head` succeeds.
    cfg = h.alembic_config_for(db.migration_url)
    command.upgrade(cfg, "head")

    migration_engine = create_engine(db.migration_url)
    try:
        with migration_engine.connect() as conn:
            head_version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        # 16. Database reaches Alembic head.
        assert head_version == "0004_stage4_priority_score"

        inspector = inspect(migration_engine)
        # 4. Stage 1A capability table exists.
        assert "application_behavior_capabilities" in inspector.get_table_names()
        # 5. Stage 1B idempotency table exists.
        assert "application_idempotency_keys" in inspector.get_table_names()

        # 6. applications.service_id exists, nullable, FK'd to admin_settings.id.
        app_columns = {c["name"]: c for c in inspector.get_columns("applications")}
        assert "service_id" in app_columns
        assert app_columns["service_id"]["nullable"] is True
        fk_names = {fk["name"] for fk in inspector.get_foreign_keys("applications")}
        assert "applications_service_id_fkey" in fk_names

        # 7 & 8. Historical application rows remain readable, with a NULL service_id.
        with migration_engine.connect() as conn:
            rows = conn.execute(
                text("SELECT id, service_id, interested_product FROM applications ORDER BY id")
            ).fetchall()
        assert len(rows) == 2
        assert all(row.service_id is None for row in rows)
        assert {row.interested_product for row in rows} == {"Legacy Detailing Package"}

        # 9. Existing admin data survives.
        with migration_engine.connect() as conn:
            admin_row = conn.execute(
                text("SELECT username FROM admins WHERE username = :u"),
                {"u": _LEGACY_ADMIN_USERNAME},
            ).fetchone()
        assert admin_row is not None

        # 10. Existing behavior metrics survive.
        with migration_engine.connect() as conn:
            metric_row = conn.execute(
                text("SELECT application_id, time_on_page, return_count FROM behavior_metrics")
            ).fetchone()
        assert metric_row.application_id == 1
        assert metric_row.time_on_page == 120
        assert metric_row.return_count == 2

        # 11. Counts unchanged except for intentionally new schema structures.
        with migration_engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM admins")).scalar_one() == 1
            assert conn.execute(text("SELECT count(*) FROM admin_settings")).scalar_one() == 1
            assert conn.execute(text("SELECT count(*) FROM applications")).scalar_one() == 2
            assert conn.execute(text("SELECT count(*) FROM behavior_metrics")).scalar_one() == 1
            assert (
                conn.execute(text("SELECT count(*) FROM application_behavior_capabilities")).scalar_one()
                == 0
            )
            assert (
                conn.execute(text("SELECT count(*) FROM application_idempotency_keys")).scalar_one() == 0
            )
    finally:
        migration_engine.dispose()

    # 15. Migration is repeatable/idempotent.
    command.upgrade(cfg, "head")
    migration_engine = create_engine(db.migration_url)
    try:
        with migration_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "0004_stage4_priority_score"
            )
    finally:
        migration_engine.dispose()

    # Finalize runtime grants (mirrors docker-compose.yml's db-roles-finalize
    # step, which runs after db-migrate) before exercising the runtime role.
    bootstrap_roles(h.role_bootstrap_config(db.name))

    # 12 & 13. Current backend starts and reads work using the runtime role.
    app_engine = create_engine(db.app_url)
    main_module_engine_backup = main_module.engine
    main_module.engine = app_engine

    def _override_get_db():
        with Session(app_engine) as session:
            yield session

    main_module.app.dependency_overrides[get_db] = _override_get_db
    try:
        with TestClient(main_module.app) as client:
            login = client.post(
                "/api/auth/login",
                json={"username": _LEGACY_ADMIN_USERNAME, "password": _LEGACY_ADMIN_PASSWORD},
            )
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            listing = client.get("/api/applications", headers=headers)
            assert listing.status_code == 200, listing.text
            listed = listing.json()
            assert len(listed) == 2
            assert all(item["service_id"] is None for item in listed)

            metrics_listing = client.get("/api/behavior-metrics", headers=headers)
            assert metrics_listing.status_code == 200, metrics_listing.text
            assert len(metrics_listing.json()) == 1

            active_services = client.get("/api/admin-settings/active")
            assert active_services.status_code == 200, active_services.text
            assert len(active_services.json()) == 1
            service_id = active_services.json()[0]["id"]

            overview = client.get("/api/analytics/overview?period=month", headers=headers)
            assert overview.status_code == 200, overview.text

            # 14. A new Stage 1B application can be created successfully.
            new_app = client.post(
                "/api/applications",
                json={
                    "first_name": "New",
                    "last_name": "Applicant",
                    "contact_data": "new@example.com",
                    "business_niche": "Личный автомобиль",
                    "company_size": "Седан или универсал",
                    "business_info": "New business",
                    "task_scope": "Разовая услуга",
                    "requester_role": "Владелец автомобиля",
                    "business_size": "Один автомобиль",
                    "need_scope": "New need",
                    "deadline": "В течение месяца",
                    "task_type": "Плановый уход",
                    "service_id": service_id,
                    "budget": "1000.00",
                    "preferred_contact_method": "Телефон",
                    "preferred_contact_time": "Утро (9:00–12:00)",
                },
            )
            assert new_app.status_code == 201, new_app.text
            assert new_app.json()["service_id"] == service_id
            assert Decimal(new_app.json()["budget"]) == Decimal("1000.00")

            final_listing = client.get("/api/applications", headers=headers)
            assert len(final_listing.json()) == 3
    finally:
        main_module.app.dependency_overrides.clear()
        main_module.engine = main_module_engine_backup
        app_engine.dispose()


def test_adoption_refuses_a_database_already_past_the_legacy_baseline(legacy_stage2_database):
    """Safety property: adopt_legacy_database must never blindly stamp a
    database that isn't actually the legacy shape - e.g. one already
    upgraded past it by some other means."""
    db = legacy_stage2_database

    with create_engine(h.admin_url().set(database=db.name)).connect() as conn:
        conn.execute(text("ALTER TABLE applications ADD COLUMN service_id INTEGER NULL"))
        conn.commit()

    with pytest.raises(LegacySchemaVerificationError):
        adopt_legacy_database(h.adoption_config(db.name))
