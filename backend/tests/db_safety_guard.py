"""Guard against running DDL (create_all/drop_all) against anything but an
explicitly-named test database.

Kept dependency-free (only needs SQLAlchemy's URL parser) so it can be unit
tested without a real database connection — see test_db_safety_guard.py.
"""

import os

from sqlalchemy.engine import make_url


class UnsafeTestDatabaseError(RuntimeError):
    """Raised when a database URL doesn't look like a dedicated test database."""


def get_test_database_url() -> str | None:
    """Return TEST_DATABASE_URL from the environment, or None.

    Deliberately has no fallback to DATABASE_URL or POSTGRES_* — those
    describe the production database and must never be used to decide
    whether, or where, to run integration tests or DDL operations.
    """
    return os.environ.get("TEST_DATABASE_URL")


def assert_safe_test_database_url(url: str, production_db_name: str | None) -> None:
    """Raise UnsafeTestDatabaseError unless `url` is unambiguously a test database.

    A target is only considered safe for DDL if its database name contains
    the marker "test" (this also covers "testing", e.g. "vibe_testing") and
    does not match the configured production POSTGRES_DB — even if that
    name happens to also contain "test".
    """
    parsed = make_url(url)
    db_name = parsed.database or ""
    normalized = db_name.strip().lower()

    if "test" not in normalized:
        raise UnsafeTestDatabaseError(
            "DDL operations blocked by test database safety guard: "
            f"database name {db_name!r} does not contain 'test'/'testing' "
            "and cannot be assumed to be a dedicated test database."
        )

    if production_db_name and normalized == production_db_name.strip().lower():
        raise UnsafeTestDatabaseError(
            "DDL operations blocked by test database safety guard: "
            f"database name {db_name!r} matches the configured production "
            "POSTGRES_DB — refusing to run create_all/drop_all against it."
        )
