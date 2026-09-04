"""FastAPI application entrypoint: startup schema guard and the top-level /api router."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DataError, SQLAlchemyError

from app import models  # noqa: F401 - registers every mapped class before first use
from app.core.database import engine
from app.core.schema_check import DatabaseNotMigratedError, ensure_database_ready
from app.routes import admin_settings, analytics, applications, auth, behavior_metrics


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Stage 2: Alembic migrations (see backend/alembic/) are the only schema
    # evolution mechanism. Startup never creates or alters anything - it only
    # verifies the database already has the schema this app version expects
    # and fails clearly (not silently) if it doesn't. See
    # app/core/schema_check.py.
    ensure_database_ready(engine)
    yield


app = FastAPI(title="Vibe Order Backend", version="0.1.0", lifespan=lifespan)


@app.exception_handler(DataError)
async def _data_error_handler(request: Request, exc: DataError) -> JSONResponse:
    # Defense-in-depth (Stage 1B): every known way client input could
    # overflow a PostgreSQL column (oversized/out-of-range numeric values,
    # string length) is already rejected by Pydantic schema bounds before
    # reaching the database (see app/schemas/application.py and
    # app/schemas/behavior_metric.py). This handler exists only as a
    # backstop so a gap in those bounds - now or in a future field - still
    # surfaces as a stable 422 for a client-triggerable data error, never an
    # unhandled 500 leaking SQL/database exception details.
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": "The request could not be processed: invalid data for one or more fields"},
    )


api_router = APIRouter(prefix="/api")


@api_router.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    # Liveness only: proves the process/event loop is alive and answering
    # requests. Deliberately does no DB work - a slow/unavailable database
    # must never make this flip unhealthy (see /ready below for that). Not
    # proxied by Nginx's production allowlist as a Docker healthcheck target
    # either - it stays the public, external liveness probe it always was;
    # see /ready's docstring for the internal-only readiness probe.
    return {"status": "ok"}


@api_router.get("/ready", tags=["health"])
def readiness_check() -> JSONResponse:
    """Stage 3 readiness probe - proves the backend can actually serve
    application traffic, not just that the process is alive (see
    health_check above for liveness). Reuses app.core.schema_check's
    existing read-only startup guard (the same check app.main's lifespan
    already runs once at boot) rather than inventing a second, possibly
    inconsistent definition of "ready": database reachable, runtime role
    usable, connected schema is the full current public application schema.
    Never performs DDL or otherwise mutates the database.

    Deliberately NOT part of the Nginx production allowlist
    (nginx/conf.d/vibe.elivcloud.org.conf only proxies /api/health) - this
    endpoint exists for the Docker Compose healthcheck (called directly
    inside the backend container, see backend/Dockerfile) and local
    operators, not the public internet. A failure never leaks connection
    details, SQL, or exception text - the body is the same generic result
    regardless of which check failed, so a caller learns only "ready" or
    "not ready".
    """
    try:
        ensure_database_ready(engine)
    except (DatabaseNotMigratedError, SQLAlchemyError):
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"status": "not_ready"}
        )
    return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "ready"})


api_router.include_router(applications.router)
api_router.include_router(behavior_metrics.router)
api_router.include_router(admin_settings.router)
api_router.include_router(auth.router)
api_router.include_router(analytics.router)

app.include_router(api_router)
