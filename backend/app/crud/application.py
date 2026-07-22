"""Database access functions for the Application entity. No FastAPI/HTTP concerns here."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError
from app.models.application import Application
from app.schemas.application import ApplicationCreate, ApplicationUpdate


def create_application(db: Session, data: ApplicationCreate) -> Application:
    application = Application(**data.model_dump())
    db.add(application)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "Could not create application due to a data integrity conflict"
        ) from exc
    db.refresh(application)
    return application


def get_application(db: Session, application_id: int) -> Application | None:
    return db.get(Application, application_id)


def get_applications(db: Session, skip: int = 0, limit: int = 100) -> list[Application]:
    stmt = select(Application).order_by(Application.id).offset(skip).limit(limit)
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
