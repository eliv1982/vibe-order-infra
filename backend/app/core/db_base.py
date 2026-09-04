"""Declarative base + naming convention only - no engine, no Settings.

Deliberately has no dependency on app.core.config (which requires
APP_DB_USER/APP_DB_PASSWORD/JWT_SECRET_KEY/... to be configured) or on
app.core.database's engine - so this module (and, transitively, app.models,
which imports Base only from here) can be imported without ever needing the
*runtime* application credential to be present. This is exactly what
backend/alembic/env.py needs: SQLAlchemy metadata to migrate/autogenerate
against, resolved with its own separate migration credential
(MIGRATION_DB_USER/MIGRATION_DB_PASSWORD), never Settings().

app.core.database re-exports Base from here (see its own module docstring)
so every existing `from app.core.database import Base` call site - every
model, tests/conftest.py - keeps working unchanged.
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# See app/core/database.py's module docstring for why this exists and why
# it's safe (matches PostgreSQL's own default constraint naming for this
# project's simple single-column constraints).
_NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "%(table_name)s_%(column_0_name)s_key",
    "ck": "%(table_name)s_%(constraint_name)s_check",
    "fk": "%(table_name)s_%(column_0_name)s_fkey",
    "pk": "%(table_name)s_pkey",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=_NAMING_CONVENTION)
