"""FastAPI application entrypoint: startup table creation and the top-level /api router."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from app import models  # noqa: F401 - registers models on Base.metadata before create_all()
from app.core.database import Base, engine
from app.routes import admin_settings, applications, behavior_metrics


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Учебный этап: таблицы создаются напрямую через metadata.create_all(),
    # без Alembic-миграций.
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Vibe Order Backend", version="0.1.0", lifespan=lifespan)

api_router = APIRouter(prefix="/api")


@api_router.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok"}


api_router.include_router(applications.router)
api_router.include_router(behavior_metrics.router)
api_router.include_router(admin_settings.router)

app.include_router(api_router)
