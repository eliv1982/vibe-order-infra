"""Database access functions for the Application entity. No FastAPI/HTTP concerns here."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, DomainValidationError, IdempotencyKeyConflictError
from app.core.security import hash_idempotency_key, hash_idempotency_payload
from app.crud import application_behavior_capability as capability_crud
from app.models.admin_setting import AdminSetting
from app.models.application import Application
from app.models.application_idempotency_key import ApplicationIdempotencyKey
from app.schemas.application import ApplicationCreate, ApplicationUpdate
from app.services.application_scoring import PriorityLevel, priority_score_bounds

# NUMERIC(12, 2) scale actually stored for Application.budget (see
# app/models/application.py) - the canonical precision every accepted
# budget value is normalized to before idempotency hashing (see
# _canonical_request_payload below).
_BUDGET_DB_SCALE = Decimal("0.01")


def _create_application_row(db: Session, data: ApplicationCreate) -> Application:
    """Validate `data.service_id` and insert the Application row (flushed,
    not committed - the caller decides when to commit).

    `with_for_update()` locks the AdminSetting row for the rest of this
    transaction, so a concurrent admin PATCH to the same service (budget
    range, is_active) cannot race between this check and the insert - it
    either already committed before this SELECT (and is reflected here), or
    it blocks until this transaction ends (see app/crud/admin_setting.py,
    whose own update goes through an ordinary UPDATE that takes the same
    row lock). This is the "one coherent DB transaction" TOCTOU fix Stage
    1B requires for service lookup + application creation.

    Raises DomainValidationError (after rolling back any pending state) if
    the service does not exist, is not active, or the budget falls outside
    its [budget_min, budget_max] - all client input-validation failures, not
    server errors. Raises ConflictError on an IntegrityError from the
    insert itself.
    """
    service = db.execute(
        select(AdminSetting).where(AdminSetting.id == data.service_id).with_for_update()
    ).scalar_one_or_none()
    # service.service_name.strip(): a historical row can hold a blank/
    # whitespace-only service_name (predates AdminSettingCreate's required-
    # text validation - see app/schemas/admin_setting.py). Such a row is
    # already excluded from GET /admin-settings/active (see
    # app/crud/admin_setting.py::get_active_admin_settings), but this
    # endpoint accepts service_id directly and must not trust that a raw
    # client only ever sends an id the active list actually returned -
    # re-checked here, under the same row lock, so it can never be used to
    # mint a new Application with a blank interested_product.
    if service is None or not service.is_active or not service.service_name.strip():
        db.rollback()
        raise DomainValidationError("Selected service is not available")
    if not (service.budget_min <= data.budget <= service.budget_max):
        db.rollback()
        raise DomainValidationError("Budget must be within the selected service's allowed range")

    application = Application(
        **data.model_dump(exclude={"service_id"}),
        service_id=service.id,
        # Denormalized snapshot of the server-verified service name - never
        # taken from the client (see app/schemas/application.py).
        interested_product=service.service_name,
    )
    db.add(application)
    try:
        db.flush()  # assigns application.id without ending the transaction
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "Could not create application due to a data integrity conflict"
        ) from exc
    return application


def create_application(db: Session, data: ApplicationCreate) -> tuple[Application, str]:
    """Create the Application together with its one-time behavior-metrics
    submission capability (see app/crud/application_behavior_capability.py),
    in a single transaction/commit - an Application can never end up
    without exactly one capability, or vice versa.

    Returns (application, raw_capability_token). The raw token exists only
    in this return value and in the client's hands afterward - it is never
    persisted, so the caller (routes/applications.py) must return it to the
    client immediately; there is no way to recover it later.

    This is the plain, non-idempotent path, used when the caller sent no
    Idempotency-Key header (see create_application_idempotent below for the
    idempotent path) - every call always creates a brand-new Application.
    """
    application = _create_application_row(db, data)
    token = capability_crud.create_capability(db, application.id)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "Could not create application due to a data integrity conflict"
        ) from exc
    db.refresh(application)
    return application, token


# --- Idempotency-Key support (Stage 1B) --------------------------------
#
# Design: an optional `Idempotency-Key` request header (validated for
# format/length in app/routes/applications.py - collision resistance
# between unrelated keys, not an entropy/authorization guarantee) lets a
# client safely retry POST /applications after a lost response without
# risking a duplicate Application. A dedicated table
# (app/models/application_idempotency_key.py) UNIQUE-constrains a SHA-256
# digest of the key - never the raw key itself, see that module's docstring
# - and stores a hash of the exact request payload it was first used with.
#
# - Same key + same payload (sequential or concurrent) -> the original
#   Application is returned, never a new one.
# - Same key + a different payload -> rejected with a stable 409 (see
#   IdempotencyKeyConflictError), never silently served against the wrong
#   payload. "Same payload" is judged on already-validated model values
#   canonicalized to their accepted business semantics (see
#   _canonical_request_payload below), not on incidental JSON/Decimal
#   serialization differences between two requests a human would consider
#   identical (e.g. budget 1000 vs 1000.0 vs "1000.00" vs "1e3").
# - The key claim (INSERT into application_idempotency_keys) and the
#   Application insert happen in the same transaction/commit, so a request
#   that fails validation (invalid service, out-of-range budget, ...) never
#   leaves a "claimed but unfulfilled" key behind - the whole attempt rolls
#   back together, and a subsequent retry with the same key starts fresh.
# - Concurrency: two callers racing with the same key both attempt to
#   INSERT the claim row. Postgres serializes this via the UNIQUE index
#   itself - the second INSERT blocks until the first's transaction ends,
#   then either succeeds (first rolled back) or raises IntegrityError (first
#   committed), so at most one caller ever becomes the "winner" that creates
#   the Application; every other caller replays its committed result.
#
# Capability handling on a replay (Stage 1B correction): a replay NEVER
# returns a usable capability, regardless of whether the original one is
# still unconsumed or was already consumed. Earlier Stage 1B behavior
# rotated the still-unconsumed capability and handed the new token back on
# replay - but the Idempotency-Key header is caller-supplied, and its
# format/length constraints (see app/routes/applications.py) bound
# collisions, not entropy: the backend has no way to distinguish a
# genuinely random client-generated key from a short-but-valid,
# attacker-guessable one, so treating a successful replay as proof of
# possession of the original request would let anyone who can guess (or
# brute-force) a key mint themselves a working behavior-metrics capability
# for someone else's application. The Idempotency-Key therefore authorizes
# nothing beyond "recognize this as a duplicate submission" - the *only*
# capability ever handed to a client is the one returned by the winning,
# first-ever creation (idempotent or not). A lost first response is
# unrecoverable by design: the caller keeps the duplicate-safe Application
# recovery an idempotent replay still provides, but not a second chance at
# the capability - see app/schemas/application.py::ApplicationCreateRead's
# docstring for the exact contract this produces.


def _canonical_request_payload(data: ApplicationCreate) -> dict[str, Any]:
    """Canonical, hash-stable form of a validated ApplicationCreate.

    `data.model_dump(mode="json")` alone is not enough for idempotency
    hashing: Pydantic preserves whatever Decimal representation the client's
    JSON produced - Decimal("1000"), Decimal("1000.0"), Decimal("1000.00")
    and Decimal("1E+3") all compare equal and are equally valid, accepted
    `budget` values, but stringify differently, so two requests a human
    would call identical could hash differently and spuriously 409. Every
    Decimal field is quantized to the exact scale the database column
    stores (NUMERIC(12, 2) - see app/models/application.py) before
    serialization, so any two accepted values are only ever hashed
    identically when they are actually the same amount. Every other field
    (required text, already stripped/normalized by the schema's
    StringConstraints; the closed Literal/categorical fields; service_id)
    has exactly one valid post-validation representation already, so no
    further canonicalization is needed for them.
    """
    payload = data.model_dump(mode="json")
    payload["budget"] = str(data.budget.quantize(_BUDGET_DB_SCALE))
    return payload


def create_application_idempotent(
    db: Session, data: ApplicationCreate, idempotency_key: str
) -> tuple[Application, str | None]:
    """Idempotent counterpart to create_application, keyed by
    `idempotency_key` (already format/length-validated by the route). See
    the module-level design note above for the full behavior contract.
    """
    key_hash = hash_idempotency_key(idempotency_key)
    request_hash = hash_idempotency_payload(_canonical_request_payload(data))

    claim = ApplicationIdempotencyKey(idempotency_key_hash=key_hash, request_hash=request_hash)
    db.add(claim)
    try:
        db.flush()
    except IntegrityError:
        # Someone already claimed this key. Postgres's unique-index insert
        # blocks until that other transaction finishes, so by the time we
        # get here it has already committed (a rolled-back claimant would
        # have let our own INSERT succeed instead) - the row is visible now.
        db.rollback()
        return _replay_existing_idempotent_request(db, key_hash, request_hash)

    # We won the claim - create the Application for real, in the same
    # transaction/commit as the claim row itself.
    application = _create_application_row(db, data)
    token = capability_crud.create_capability(db, application.id)
    claim.application_id = application.id

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "Could not create application due to a data integrity conflict"
        ) from exc
    db.refresh(application)
    return application, token


def _replay_existing_idempotent_request(
    db: Session, key_hash: str, request_hash: str
) -> tuple[Application, str | None]:
    """Handle a POST /applications call whose Idempotency-Key was already
    claimed by a (now-committed) prior request. `key_hash` is the SHA-256
    digest of the raw key (see hash_idempotency_key) - never the raw key
    itself.

    `with_for_update()` also serializes this against any other concurrent
    replay of the same key, and against the still-in-flight winning
    transaction that first claimed it (see create_application_idempotent) -
    so this never observes a claim row whose application_id hasn't been set
    yet.

    Always returns capability=None (see the module-level design note above
    for why a replay must never be treated as authorization to mint or
    recover a capability) - this function only ever reads state, it never
    creates or rotates a capability.
    """
    stmt = (
        select(ApplicationIdempotencyKey)
        .where(ApplicationIdempotencyKey.idempotency_key_hash == key_hash)
        .with_for_update()
    )
    existing = db.execute(stmt).scalar_one_or_none()
    if existing is None:
        # Vanishingly unlikely: the transaction that briefly held this key
        # rolled back in full after all, between our failed INSERT and this
        # SELECT. Nothing to replay - ask the client to retry.
        db.rollback()
        raise ConflictError("Could not process the request due to a concurrent conflict - please retry")

    if existing.request_hash != request_hash:
        db.rollback()
        raise IdempotencyKeyConflictError(
            "This Idempotency-Key was already used with a different request"
        )

    application = get_application(db, existing.application_id) if existing.application_id else None
    if application is None:
        db.rollback()
        raise ConflictError("The original application for this Idempotency-Key no longer exists")

    db.commit()
    db.refresh(application)
    return application, None


def get_application(db: Session, application_id: int) -> Application | None:
    return db.get(Application, application_id)


def get_applications(db: Session, skip: int = 0, limit: int = 100) -> list[Application]:
    stmt = select(Application).order_by(Application.id).offset(skip).limit(limit)
    return list(db.scalars(stmt).all())


# Search fields (Stage 4 correction): exactly the haystack the admin panel's
# search box previously matched client-side, in JavaScript, against only
# whatever page happened to already be loaded (frontend/src/pages/
# adminApplications.ts's now-removed matchesSearch) - moved here verbatim so
# the *meaning* of a search query does not change, only where it runs.
_SEARCH_COLUMNS: tuple[str, ...] = (
    "first_name",
    "last_name",
    "contact_data",
    "interested_product",
    "business_info",
)

# The character PostgreSQL is told (via the ILIKE ... ESCAPE clause below) to
# treat as an escape prefix - never anything user-supplied, so a search query
# can never redefine it.
_LIKE_ESCAPE_CHAR = "\\"


def _escape_like_wildcards(value: str) -> str:
    """Makes `value` safe to embed inside a `%...%` ILIKE pattern as a
    literal substring: a search for e.g. "50%" or "a_b" must match those
    exact characters, not PostgreSQL's LIKE wildcards - so any `%`, `_` or
    literal backslash the admin typed is escaped first (backslash doubled
    before the other two, so an already-escaped sequence in the input can't
    be reinterpreted). Paired with `.ilike(pattern, escape=_LIKE_ESCAPE_CHAR)`
    at every call site below."""
    return (
        value.replace(_LIKE_ESCAPE_CHAR, _LIKE_ESCAPE_CHAR * 2)
        .replace("%", f"{_LIKE_ESCAPE_CHAR}%")
        .replace("_", f"{_LIKE_ESCAPE_CHAR}_")
    )


def _prioritized_filters(
    search: str | None, priority: PriorityLevel | None
) -> list[ColumnElement[bool]]:
    """WHERE-clause fragments shared by get_prioritized_applications_page's
    COUNT and page queries, so both are always computed against the exact
    same filtered corpus (see that function's docstring). Returns an empty
    list - a no-op when applied via `.where(*filters)` - when neither
    criterion is active."""
    filters: list[ColumnElement[bool]] = []

    normalized_search = (search or "").strip()
    if normalized_search:
        pattern = f"%{_escape_like_wildcards(normalized_search)}%"
        filters.append(
            or_(
                *(
                    getattr(Application, column).ilike(pattern, escape=_LIKE_ESCAPE_CHAR)
                    for column in _SEARCH_COLUMNS
                )
            )
        )

    if priority is not None:
        low, high = priority_score_bounds(priority)
        filters.append(Application.priority_score.between(low, high))

    return filters


def get_prioritized_applications_page(
    db: Session,
    skip: int,
    limit: int,
    search: str | None = None,
    priority: PriorityLevel | None = None,
) -> tuple[list[Application], int]:
    """One page of applications, ordered by the materialized priority_score
    (descending), then created_at (ascending), then id (ascending) as a
    deterministic tie-break - exactly the order routes/applications.py used
    to compute in Python over the *entire* table on every request (see
    Application.priority_score's docstring in app/models/application.py for
    how that column is kept in sync). Ordering, OFFSET and LIMIT all happen
    in PostgreSQL: this only ever hydrates the `limit` rows actually
    returned, never the whole applications table.

    `search`/`priority` (Stage 4 correction): optional server-side criteria
    - see _prioritized_filters above for exactly what each matches. Applied
    identically to both the COUNT and the row query below, and *before*
    OFFSET/LIMIT, so `total` and every page's rows always describe the same
    filtered corpus - never the unfiltered table with only the current page
    narrowed down client-side (the defect this correction fixes: search/
    priority used to filter whatever 100-row page was already loaded in the
    browser, so a match that existed only on a later page was invisible from
    page 1, and a genuinely page-1-empty search result was indistinguishable
    from a corpus-wide one).

    Returns (page, total) - `total` is the filtered row count (a single
    SELECT COUNT(*) over the same WHERE clause, independent of skip/limit)
    so callers can build pagination metadata (see PrioritizedApplicationList)
    without a second unpaginated fetch.
    """
    filters = _prioritized_filters(search, priority)

    total = db.scalar(select(func.count()).select_from(Application).where(*filters)) or 0
    stmt = (
        select(Application)
        .where(*filters)
        .order_by(
            Application.priority_score.desc(),
            Application.created_at.asc(),
            Application.id.asc(),
        )
        .offset(skip)
        .limit(limit)
    )
    items = list(db.scalars(stmt).all())
    return items, total


def get_applications_created_between(
    db: Session, start: datetime, end: datetime
) -> list[Application]:
    """Applications with created_at in the half-open [start, end) window.

    Used by the analytics overview endpoint to pre-scope the period at the
    DB level; app.services.behavior_analytics re-validates the exact
    boundary itself (see its module docstring) so that logic stays
    unit-testable independent of this query.
    """
    stmt = (
        select(Application)
        .where(Application.created_at >= start, Application.created_at < end)
        .order_by(Application.id)
    )
    return list(db.scalars(stmt).all())


def update_application(
    db: Session, application_id: int, data: ApplicationUpdate
) -> Application | None:
    application = get_application(db, application_id)
    if application is None:
        return None
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(application, field, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "Could not update application due to a data integrity conflict"
        ) from exc
    db.refresh(application)
    return application


def delete_application(db: Session, application_id: int) -> bool:
    application = get_application(db, application_id)
    if application is None:
        return False
    db.delete(application)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "Could not delete application due to a data integrity conflict"
        ) from exc
    return True
