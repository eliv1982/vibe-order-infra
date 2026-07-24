"""Verifies the OpenAPI schema correctly declares HTTP Bearer auth for /api/auth/me.

No database needed - generating the OpenAPI schema doesn't touch the DB
(SQLAlchemy engines connect lazily, and TestClient is used without a `with`
block so the app's lifespan never runs), so this always runs regardless of
TEST_DATABASE_URL, matching how routes/auth.py wires HTTPBearer for Swagger's
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
