"""Database access for one-time behavior-metrics submission capabilities.

See app/models/application_behavior_capability.py for what a row here
proves and app/core/security.py for how the raw token is generated/hashed.
No FastAPI/HTTP concerns here, matching the rest of the crud/ layer.
"""

from sqlalchemy import func, update
from sqlalchemy.orm import Session

from app.core.security import generate_capability_token, hash_capability_token
from app.models.application_behavior_capability import ApplicationBehaviorCapability


def create_capability(db: Session, application_id: int) -> str:
    """Create (but do not commit) a new one-time capability for
    `application_id` and return the raw token.

    Only the digest is persisted - this is the only place the raw token is
    ever available, so the caller (app.crud.application.create_application)
    must hand it back to the client before it's lost for good. Deliberately
    does not commit: it must land in the same transaction as the Application
    row it belongs to, so an Application can never exist without exactly
    one capability, and vice versa.
    """
    token = generate_capability_token()
    db.add(
        ApplicationBehaviorCapability(
            application_id=application_id,
            capability_hash=hash_capability_token(token),
        )
    )
    return token


def try_consume_capability(db: Session, application_id: int, token: str | None) -> bool:
    """Atomically mark the capability for `application_id` as used, iff
    `token` matches its stored digest and it has not already been used.

    Returns True only when *this* call performed that used_at NULL -> now()
    transition - the caller may then proceed to insert the behavior-metric
    row. Returns False for a nonexistent application_id (no capability row
    was ever created for it, or it was cascade-deleted with the
    application), a missing/empty/wrong token, or a capability that was
    already consumed (including a concurrent
    same-token replay racing this exact call: the UPDATE's WHERE clause,
    including `used_at IS NULL`, makes two concurrent callers unable to both
    receive True - Postgres locks the matched row for the first UPDATE to
    reach it, and by the time the second is unblocked it re-evaluates the
    WHERE clause against the now-committed row and finds used_at no longer
    NULL, matching zero rows).

    Deliberately does not commit - the caller must commit (or roll back)
    this UPDATE together with whatever it does next with a True result (see
    app/routes/behavior_metrics.py), so a capability can never end up marked
    used except atomically alongside the behavior-metric row it authorizes.
    """
    if not token:
        return False

    stmt = (
        update(ApplicationBehaviorCapability)
        .where(
            ApplicationBehaviorCapability.application_id == application_id,
            ApplicationBehaviorCapability.capability_hash == hash_capability_token(token),
            ApplicationBehaviorCapability.used_at.is_(None),
        )
        .values(used_at=func.now())
    )
    result = db.execute(stmt)
    return result.rowcount == 1
