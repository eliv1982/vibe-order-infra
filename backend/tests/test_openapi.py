"""Verifies the OpenAPI schema declares HTTP Bearer auth on protected operations
(and *not* on public ones) across /api/auth/me and the now-protected admin CRUD.

No database needed - generating the OpenAPI schema doesn't touch the DB
(SQLAlchemy engines connect lazily, and TestClient is used without a `with`
block so the app's lifespan never runs), so this always runs regardless of
TEST_DATABASE_URL, matching how the `dependencies=[Depends(get_current_admin)]`
wiring on protected routes surfaces the same HTTPBearer scheme for Swagger's
"Authorize" flow.
"""

from fastapi.testclient import TestClient

from app.main import app


def test_me_endpoint_declares_http_bearer_security():
    schema = TestClient(app).get("/openapi.json").json()

    me_operation = schema["paths"]["/api/auth/me"]["get"]
    assert me_operation.get("security") == [{"HTTPBearer": []}]

    security_schemes = schema["components"]["securitySchemes"]
    assert security_schemes["HTTPBearer"] == {"type": "http", "scheme": "bearer"}


def test_admin_settings_active_endpoint_has_no_security_requirement():
    schema = TestClient(app).get("/openapi.json").json()
    operation = schema["paths"]["/api/admin-settings/active"]["get"]
    assert not operation.get("security")


def test_admin_settings_list_endpoint_requires_bearer():
    schema = TestClient(app).get("/openapi.json").json()
    operation = schema["paths"]["/api/admin-settings"]["get"]
    assert operation.get("security") == [{"HTTPBearer": []}]


def test_applications_create_endpoint_has_no_security_requirement():
    schema = TestClient(app).get("/openapi.json").json()
    operation = schema["paths"]["/api/applications"]["post"]
    assert not operation.get("security")


def test_applications_list_endpoint_requires_bearer():
    schema = TestClient(app).get("/openapi.json").json()
    operation = schema["paths"]["/api/applications"]["get"]
    assert operation.get("security") == [{"HTTPBearer": []}]


def test_applications_prioritized_endpoint_requires_bearer():
    schema = TestClient(app).get("/openapi.json").json()
    operation = schema["paths"]["/api/applications/prioritized"]["get"]
    assert operation.get("security") == [{"HTTPBearer": []}]


def test_behavior_metrics_create_endpoint_has_no_security_requirement():
    schema = TestClient(app).get("/openapi.json").json()
    operation = schema["paths"]["/api/behavior-metrics"]["post"]
    assert not operation.get("security")


def test_behavior_metrics_list_endpoint_requires_bearer():
    schema = TestClient(app).get("/openapi.json").json()
    operation = schema["paths"]["/api/behavior-metrics"]["get"]
    assert operation.get("security") == [{"HTTPBearer": []}]
