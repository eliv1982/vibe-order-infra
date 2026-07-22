"""HTTP routes for the BehaviorMetric entity: validate via schemas, delegate logic to crud."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import ConflictError
from app.crud import application as application_crud
from app.crud import behavior_metric as crud
from app.schemas.behavior_metric import (
    BehaviorMetricCreate,
    BehaviorMetricRead,
    BehaviorMetricUpdate,
)

router = APIRouter(prefix="/behavior-metrics", tags=["behavior-metrics"])


@router.post("", response_model=BehaviorMetricRead, status_code=status.HTTP_201_CREATED)
def create_behavior_metric(
    payload: BehaviorMetricCreate, db: Session = Depends(get_db)
) -> BehaviorMetricRead:
    if application_crud.get_application(db, payload.application_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")
    if crud.get_behavior_metric_by_application(db, payload.application_id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Behavior metric already exists for this application",
        )
    try:
        return crud.create_behavior_metric(db, payload)
    except ConflictError as exc:
        # Final defense against a race between the checks above and this
        # insert (DB unique constraint) — translated to 409, not 500.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("", response_model=list[BehaviorMetricRead])
def list_behavior_metrics(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[BehaviorMetricRead]:
    return crud.get_behavior_metrics(db, skip=skip, limit=limit)


@router.get("/{metric_id}", response_model=BehaviorMetricRead)
def get_behavior_metric(metric_id: int, db: Session = Depends(get_db)) -> BehaviorMetricRead:
    metric = crud.get_behavior_metric(db, metric_id)
    if metric is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Behavior metric not found")
    return metric


@router.patch("/{metric_id}", response_model=BehaviorMetricRead)
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


@router.delete("/{metric_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_behavior_metric(metric_id: int, db: Session = Depends(get_db)) -> None:
    try:
        deleted = crud.delete_behavior_metric(db, metric_id)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Behavior metric not found")
