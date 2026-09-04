"""HTTP routes for the Application entity: validate via schemas, delegate logic to crud."""

import re
from datetime import datetime, timezone

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
from app.services.application_scoring import ApplicationScore, score_application

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


def _created_at_sort_key(created_at: datetime | None) -> tuple[int, datetime]:
    """Normalize created_at into a sort key that is safe to compare.

    Returns (presence_flag, normalized_datetime):

    - presence_flag=0 for a known created_at, sorted by the normalized,
      UTC-aware datetime ascending;
    - presence_flag=1 if created_at is unexpectedly None, always sorting
      after every known date (tuple comparison short-circuits on this
      first element, so the placeholder datetime alongside it is never
      actually compared against a real one).

    A naive datetime is deterministically treated as UTC (for this sort
    only - no claim is made about its true origin) so it can be compared
    against timezone-aware values without Python's "can't compare
    offset-naive and offset-aware datetimes" TypeError. This compares
    datetimes directly rather than converting to POSIX timestamps, so it
    avoids platform-sensitive overflow issues (e.g. datetime.min.timestamp()
    on Windows).
    """
    if created_at is None:
        return (1, datetime.min.replace(tzinfo=timezone.utc))
    if created_at.tzinfo is None:
        return (0, created_at.replace(tzinfo=timezone.utc))
    return (0, created_at.astimezone(timezone.utc))


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
    db: Session = Depends(get_db),
) -> PrioritizedApplicationList:
    # Declared before GET /{application_id} so "prioritized" is matched
    # here rather than being captured by that dynamic int path.
    #
    # Учебный этап: набор заявок мал, поэтому весь список загружается в
    # память и сортируется в Python. При заметном росте объёма данных
    # потребуется materialized scoring на уровне БД (например, хранимая
    # колонка/вьюха) или отдельная стратегия пагинации, считающая score
    # порциями, а не через полную выборку.
    applications = crud.get_all_applications(db)

    scored = [(application, score_application(application)) for application in applications]
    scored.sort(
        key=lambda pair: (
            -pair[1].score,
            _created_at_sort_key(pair[0].created_at),
            pair[0].id,
        )
    )

    total = len(scored)
    page = scored[skip : skip + limit]

    return PrioritizedApplicationList(
        items=[_to_priority_read(application, score) for application, score in page],
        total=total,
        skip=skip,
        limit=limit,
    )


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
