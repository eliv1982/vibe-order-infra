"""HTTP routes for the Application entity: validate via schemas, delegate logic to crud."""

import re

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_admin
from app.core.exceptions import ConflictError, DomainValidationError, IdempotencyKeyConflictError
from app.crud import application as crud
from app.models.application import Application
from app.schemas.application import (
    ApplicationCreate,
    ApplicationCreateRead,
    ApplicationRead,
    ApplicationUpdate,
)
from app.schemas.application_analysis import (
    ApplicationPriorityRead,
    PrioritizedApplicationList,
    ScoringReason,
)
from app.services.application_scoring import ApplicationScore, PriorityLevel, score_application

# Bounds a search query to a sane length before it ever reaches the database
# (Stage 4 correction) - generous enough for any realistic name/contact/
# service/vehicle-description substring, but small enough to keep the
# ILIKE scan (see crud.get_prioritized_applications_page) cheap regardless of
# who's typing.
_SEARCH_MAX_LENGTH = 200

router = APIRouter(prefix="/applications", tags=["applications"])

# Bounded format for the optional Idempotency-Key header (Stage 1B
# correction): a restricted charset and a minimum length, chosen for
# collision resistance between unrelated keys - NOT as an entropy/
# authorization requirement. The backend cannot tell a genuinely random
# client-supplied key from a short, guessable-but-format-valid one, so a
# successful replay is never treated as proof of possession of anything: it
# only recognizes a duplicate submission and returns the original
# Application, never a behavior-metrics capability (see
# app/crud/application.py::create_application_idempotent's module-level
# design note - a capability is only ever handed out once, on the winning
# first-ever creation). 43 characters is the length secrets.token_urlsafe(32)
# always produces - kept as the floor because it comfortably avoids
# accidental collisions between distinct legitimate submissions, not because
# a caller-chosen key needs to resist brute force. Only a SHA-256 digest of
# this key is ever persisted (app/core/security.hash_idempotency_key,
# app/models/application_idempotency_key.py). The frontend generates exactly
# this shape (see frontend/src/utils/idempotencyKey.ts); never logged
# unnecessarily (see app/crud/application.py's module docstring), and never
# reflected back in any response.
_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")


def _to_priority_read(application: Application, score: ApplicationScore) -> ApplicationPriorityRead:
    return ApplicationPriorityRead(
        application=ApplicationRead.model_validate(application),
        priority_score=score.score,
        priority_level=score.level,
        priority_label=score.label,
        reasons=[
            ScoringReason(code=reason.code, points=reason.points, label=reason.label)
            for reason in score.reasons
        ],
        recommended_action=score.recommended_action,
        recommended_team=score.recommended_team,
        requires_personal_manager=score.requires_personal_manager,
    )


@router.post("", response_model=ApplicationCreateRead, status_code=status.HTTP_201_CREATED)
def create_application(
    payload: ApplicationCreate,
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ApplicationCreateRead:
    # Public: this is the client-facing application form's submit endpoint.
    # The response includes a behavior_metrics_capability the client needs
    # to submit behavior metrics for this application (see
    # POST /behavior-metrics) - normally a one-time token, but see
    # ApplicationCreateRead's docstring for the (nullable) idempotent-retry
    # case.
    #
    # Idempotency-Key (Stage 1B) is entirely optional: a caller that omits
    # it gets exactly the prior, non-idempotent behavior (a brand-new
    # Application every call) - see app/crud/application.py for the full
    # idempotency design.
    if idempotency_key is not None and not _IDEMPOTENCY_KEY_RE.fullmatch(idempotency_key):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Idempotency-Key must be 43-128 characters from [A-Za-z0-9_-]",
        )

    try:
        if idempotency_key is not None:
            application, capability_token = crud.create_application_idempotent(
                db, payload, idempotency_key
            )
        else:
            application, capability_token = crud.create_application(db, payload)
    except IdempotencyKeyConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except DomainValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return ApplicationCreateRead(
        **ApplicationRead.model_validate(application).model_dump(),
        behavior_metrics_capability=capability_token,
    )


@router.get("", response_model=list[ApplicationRead], dependencies=[Depends(get_current_admin)])
def list_applications(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[ApplicationRead]:
    return crud.get_applications(db, skip=skip, limit=limit)


@router.get(
    "/prioritized",
    response_model=PrioritizedApplicationList,
    dependencies=[Depends(get_current_admin)],
)
def list_prioritized_applications(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    search: str | None = Query(None, max_length=_SEARCH_MAX_LENGTH),
    priority: PriorityLevel | None = Query(None),
    db: Session = Depends(get_db),
) -> PrioritizedApplicationList:
    # Declared before GET /{application_id} so "prioritized" is matched
    # here rather than being captured by that dynamic int path.
    #
    # Stage 4: ordering, OFFSET and LIMIT all run in PostgreSQL against the
    # materialized Application.priority_score column (see
    # crud.get_prioritized_applications_page and that column's docstring in
    # app/models/application.py) - only the `limit` rows actually returned
    # are ever loaded into Python, never the whole applications table.
    # score_application() is still called here, but only over that same
    # small page, to build each item's full explanation (reasons,
    # recommended_action, ...) - priority_score itself only carries the
    # sortable number, not the breakdown.
    #
    # search/priority (Stage 4 correction): optional server-side criteria,
    # both omitted by default so an existing caller that never sends them
    # keeps getting the exact same unfiltered, paginated result as before.
    # Applied inside get_prioritized_applications_page before COUNT and
    # OFFSET/LIMIT, so `total` below is the filtered corpus size whenever a
    # criterion is active - never the whole table's count - and pagination
    # walks the filtered result set, not the unfiltered one. `priority` is
    # validated as one of the closed PriorityLevel values by FastAPI itself
    # (an unrecognized value is a 422, not a silently-ignored filter).
    applications, total = crud.get_prioritized_applications_page(
        db, skip=skip, limit=limit, search=search, priority=priority
    )

    items = [
        _to_priority_read(application, score_application(application)) for application in applications
    ]

    return PrioritizedApplicationList(items=items, total=total, skip=skip, limit=limit)


@router.get(
    "/{application_id}", response_model=ApplicationRead, dependencies=[Depends(get_current_admin)]
)
def get_application(application_id: int, db: Session = Depends(get_db)) -> ApplicationRead:
    application = crud.get_application(db, application_id)
    if application is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")
    return application


@router.patch(
    "/{application_id}", response_model=ApplicationRead, dependencies=[Depends(get_current_admin)]
)
def update_application(
    application_id: int, payload: ApplicationUpdate, db: Session = Depends(get_db)
) -> ApplicationRead:
    try:
        application = crud.update_application(db, application_id, payload)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if application is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")
    return application


@router.delete(
    "/{application_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(get_current_admin)],
)
def delete_application(application_id: int, db: Session = Depends(get_db)) -> None:
    try:
        deleted = crud.delete_application(db, application_id)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")
