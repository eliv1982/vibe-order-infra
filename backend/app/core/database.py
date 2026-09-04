"""SQLAlchemy engine, session factory, declarative base and the FastAPI DB dependency.

Stage 2: `engine` is built from the *runtime* application credential only
(Settings.database_url - APP_DB_USER/APP_DB_PASSWORD, see
app/core/config.py) - never the migration/owner credential. `Base` itself
(the declarative base + naming convention) lives in app.core.db_base and is
just re-exported here, so it can be imported - by every model, and by
Alembic (backend/alembic/env.py) - without requiring the runtime credential
or JWT_SECRET_KEY to be configured at all.
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.db_base import Base  # noqa: F401 - re-exported; see module docstring
from app.core.schema_introspect import APPLICATION_SCHEMA

settings = get_settings()

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    # Stage 2 MAJOR correction: every SQLAlchemy ORM statement this app
    # issues (app/models/*.py) is unqualified - it relies entirely on the
    # connection's search_path to resolve to "public". PostgreSQL's own
    # default search_path is "$user", public, so a schema that happens to
    # share the runtime role's name would otherwise silently shadow public
    # for every query. `options=-c search_path=...` is a libpq/psycopg
    # connection-startup parameter (not a SET issued by this app, and not
    # DDL) applied on every new pooled connection; per PostgreSQL's own GUC
    # precedence it is applied *after* any ALTER ROLE/DATABASE ... SET
    # search_path default, so it deterministically wins over both
    # PostgreSQL's own "$user" default and any role-level override. See
    # app.core.schema_check's current_schema() startup guard for the
    # read-only, fail-closed proof that this actually took effect.
    connect_args={"options": f"-c search_path={APPLICATION_SCHEMA}"},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
