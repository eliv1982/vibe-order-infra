"""HTTP routes for the BehaviorMetric entity: validate via schemas, delegate logic to crud."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_admin
from app.core.exceptions import ConflictError
from app.crud import application_behavior_capability as capability_crud
from app.crud import behavior_metric as crud
from app.schemas.behavior_metric import (
    BehaviorMetricCreate,
    BehaviorMetricRead,
    BehaviorMetricUpdate,
)

router = APIRouter(prefix="/behavior-metrics", tags=["behavior-metrics"])

# Single stable response for "no currently-valid capability was proven for
# this application_id" - see create_behavior_metric below for why this is
# the *only* failure mode that endpoint can ever report to an unauthenticated
# caller, regardless of the underlying reason.
_INVALID_CAPABILITY_DETAIL = "Invalid or already-used capability"


@router.post("", response_model=BehaviorMetricRead, status_code=status.HTTP_201_CREATED)
def create_behavior_metric(
    payload: BehaviorMetricCreate, db: Session = Depends(get_db)
) -> BehaviorMetricRead:
    # Public: the client form sends this right after creating an application,
    # using the one-time capability that POST /applications returned for it.
    # No admin token is required or accepted here - the capability is the
    # only thing that authorizes this submission (see
    # app/crud/application_behavior_capability.py), and it authorizes
    # nothing else.
    #
    # Deliberately does NOT check application/metric existence before (or
    # instead of) validating the capability - that used to let an
    # unauthenticated caller without a valid capability tell apart a
    # nonexistent application_id (404), an existing one with the wrong
    # capability (403), and one that already has metrics (409), which leaks
    # application existence and metric state to anyone who can guess or
    # enumerate ids. The single atomic capability-consumption attempt below
    # is now the *only* thing this handler does before insertion, and its
    # failure path is the *only* error response this endpoint can produce
    # for any of: a nonexistent application_id (no capability row was ever
    # created for it), an existing application with a wrong/missing/empty
    # token, a capability that belongs to a different application, a
    # capability that was already consumed (including by a prior successful
    # submission - i.e. "metrics already exist" no longer gets its own
    # response), and a historical application that has no capability row at
    # all (e.g. inserted directly, bypassing crud.application.create_application).
    # All of these collapse into the exact same status code and body.
    if not capability_crud.try_consume_capability(db, payload.application_id, payload.capability):
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=_INVALID_CAPABILITY_DETAIL
        )

    try:
        # The capability's used_at UPDATE above and this INSERT share the
        # same session/transaction and are committed together here - if the
        # insert fails, both roll back together, so a capability can never
        # be permanently burned by an unrelated insert failure. This is a
        # different situation from the check above: reaching this point
        # already proved possession of a genuinely valid, just-consumed
        # capability, so a distinct response here reveals nothing to a
        # caller who never had one - it can only ever fire for the rare case
        # of the application being deleted concurrently between the two
        # statements.
        return crud.create_behavior_metric(db, payload)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("", response_model=list[BehaviorMetricRead], dependencies=[Depends(get_current_admin)])
def list_behavior_metrics(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[BehaviorMetricRead]:
    return crud.get_behavior_metrics(db, skip=skip, limit=limit)


@router.get(
    "/{metric_id}", response_model=BehaviorMetricRead, dependencies=[Depends(get_current_admin)]
)
def get_behavior_metric(metric_id: int, db: Session = Depends(get_db)) -> BehaviorMetricRead:
    metric = crud.get_behavior_metric(db, metric_id)
    if metric is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Behavior metric not found")
    return metric


@router.patch(
    "/{metric_id}", response_model=BehaviorMetricRead, dependencies=[Depends(get_current_admin)]
)
def update_behavior_metric(
    metric_id: int, payload: BehaviorMetricUpdate, db: Session = Depends(get_db)
) -> BehaviorMetricRead:
    try:
        metric = crud.update_behavior_metric(db, metric_id, payload)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if metric is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Behavior metric not found")
    return metric


@router.delete(
    "/{metric_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(get_current_admin)],
)
def delete_behavior_metric(metric_id: int, db: Session = Depends(get_db)) -> None:
    try:
        deleted = crud.delete_behavior_metric(db, metric_id)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Behavior metric not found")
