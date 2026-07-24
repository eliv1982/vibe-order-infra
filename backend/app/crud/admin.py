"""Database access functions for the Admin entity. No FastAPI/HTTP concerns here."""

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, DomainValidationError
from app.core.security import hash_password
from app.models.admin import Admin

# Arbitrary fixed bigint identifying the "first admin registration" advisory
# lock. Transaction-scoped (pg_advisory_xact_lock) so it's released
# automatically on commit/rollback. This is the only advisory lock this app
# uses today - if another one is ever added, it must use a different key.
_FIRST_ADMIN_LOCK_KEY = 9_184_726_501


def get_admin(db: Session, admin_id: int) -> Admin | None:
    return db.get(Admin, admin_id)


def get_admin_by_username(db: Session, username_normalized: str) -> Admin | None:
    stmt = select(Admin).where(Admin.username == username_normalized)
    return db.scalars(stmt).first()


def count_admins(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Admin)) or 0


def register_first_admin(db: Session, username_normalized: str, password: str) -> Admin:
    """Atomically create the first Admin row, or raise ConflictError if one already exists.

    A plain UNIQUE constraint on username doesn't stop this race: two
    concurrent requests could register two *different* usernames, and both
    would satisfy uniqueness while violating "only one admin, ever". Instead,
    every call first takes a Postgres transaction-scoped advisory lock keyed
    by _FIRST_ADMIN_LOCK_KEY, serializing all concurrent callers onto the
    same check-then-insert path: whichever call commits first releases the
    lock and the row becomes visible; the next call to acquire the lock then
    re-checks the count, sees it's no longer zero, and raises without
    inserting. This is safe under the default READ COMMITTED isolation level.

    username_normalized is expected to already be normalized (see
    app.core.security.normalize_username), which the Pydantic schema layer
    guarantees for real requests. This is just a defensive backstop for a
    caller that invokes this function directly, bypassing that schema - it
    doesn't re-run normalization, only rejects blank input outright.
    """
    if not username_normalized or not username_normalized.strip():
        raise DomainValidationError("username_normalized must not be empty or whitespace-only")

    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _FIRST_ADMIN_LOCK_KEY})

    if count_admins(db) > 0:
        db.rollback()
        raise ConflictError("An administrator already exists; registration is closed")

    admin = Admin(username=username_normalized, password_hash=hash_password(password))
    db.add(admin)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("An administrator already exists; registration is closed") from exc
    db.refresh(admin)
    return admin
