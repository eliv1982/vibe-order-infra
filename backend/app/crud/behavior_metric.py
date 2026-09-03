"""Database access functions for the BehaviorMetric entity. No FastAPI/HTTP concerns here."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError
from app.models.behavior_metric import BehaviorMetric
from app.schemas.behavior_metric import BehaviorMetricCreate, BehaviorMetricUpdate


def create_behavior_metric(db: Session, data: BehaviorMetricCreate) -> BehaviorMetric:
    # capability is a write-only field with no matching column on
    # BehaviorMetric (see schemas/behavior_metric.py) - it must already have
    # been verified/consumed by the caller (routes/behavior_metrics.py)
    # before this is ever called.
    metric = BehaviorMetric(**data.model_dump(exclude={"capability"}))
    db.add(metric)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # Final defense against a race between the caller's existence/
        # uniqueness checks and this insert: either the application was
        # deleted concurrently, or another request created the metric first.
        raise ConflictError(
            "Behavior metric could not be created: duplicate application_id "
            "or the application no longer exists"
        ) from exc
    db.refresh(metric)
    return metric


def get_behavior_metric(db: Session, metric_id: int) -> BehaviorMetric | None:
    return db.get(BehaviorMetric, metric_id)


def get_behavior_metric_by_application(
    db: Session, application_id: int
) -> BehaviorMetric | None:
    stmt = select(BehaviorMetric).where(BehaviorMetric.application_id == application_id)
    return db.scalars(stmt).first()


def get_behavior_metrics(db: Session, skip: int = 0, limit: int = 100) -> list[BehaviorMetric]:
    stmt = select(BehaviorMetric).order_by(BehaviorMetric.id).offset(skip).limit(limit)
    return list(db.scalars(stmt).all())


def get_behavior_metrics_by_application(db: Session, application_id: int) -> list[BehaviorMetric]:
    """All metric rows linked to one application.

    application_id is UNIQUE (see app/models/behavior_metric.py), so this
    currently returns at most one row; it stays list-shaped so the
    analytics detail endpoint's aggregation is well-defined even if that
    constraint is ever relaxed (see build_application_detail).
    """
    stmt = (
        select(BehaviorMetric)
        .where(BehaviorMetric.application_id == application_id)
        .order_by(BehaviorMetric.id)
    )
    return list(db.scalars(stmt).all())


def get_behavior_metrics_created_between(
    db: Session, start: datetime, end: datetime
) -> list[BehaviorMetric]:
    """Metrics with created_at in the half-open [start, end) window.

    Used by the analytics overview endpoint to pre-scope the period at the
    DB level; app.services.behavior_analytics re-validates the exact
    boundary itself so that logic stays unit-testable independent of this
    query.
    """
    stmt = (
        select(BehaviorMetric)
        .where(BehaviorMetric.created_at >= start, BehaviorMetric.created_at < end)
        .order_by(BehaviorMetric.id)
    )
    return list(db.scalars(stmt).all())


def update_behavior_metric(
    db: Session, metric_id: int, data: BehaviorMetricUpdate
) -> BehaviorMetric | None:
    metric = get_behavior_metric(db, metric_id)
    if metric is None:
        return None
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(metric, field, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "Could not update behavior metric due to a data integrity conflict"
        ) from exc
    db.refresh(metric)
    return metric


def delete_behavior_metric(db: Session, metric_id: int) -> bool:
    metric = get_behavior_metric(db, metric_id)
    if metric is None:
        return False
    db.delete(metric)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "Could not delete behavior metric due to a data integrity conflict"
        ) from exc
    return True
