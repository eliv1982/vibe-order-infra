"""FastAPI application entrypoint: startup table creation and the top-level /api router."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DataError

from app import models  # noqa: F401 - registers models on Base.metadata before create_all()
from app.core.database import Base, engine
from app.core.schema_compat import upgrade_applications_service_id
from app.routes import admin_settings, analytics, applications, auth, behavior_metrics


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Учебный этап: таблицы создаются напрямую через metadata.create_all(),
    # без Alembic-миграций. upgrade_applications_service_id() is a narrow,
    # idempotent exception to that: it ALTERs an *existing* Stage 1A
    # `applications` table to add `service_id` (create_all() only CREATEs
    # missing tables, it never ALTERs one that's already there) - see
    # app/core/schema_compat.py. Must run before create_all() so a brand-new
    # database still gets the column via the normal model-driven CREATE
    # TABLE, with nothing left for this step to do.
    upgrade_applications_service_id(engine)
    Base.metadata.create_all(bind=engine)
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
    return {"status": "ok"}


api_router.include_router(applications.router)
api_router.include_router(behavior_metrics.router)
api_router.include_router(admin_settings.router)
api_router.include_router(auth.router)
api_router.include_router(analytics.router)

app.include_router(api_router)
