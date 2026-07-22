"""Database access functions for the AdminSetting entity. No FastAPI/HTTP concerns here."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, DomainValidationError
from app.models.admin_setting import AdminSetting
from app.schemas.admin_setting import AdminSettingCreate, AdminSettingUpdate


def create_admin_setting(db: Session, data: AdminSettingCreate) -> AdminSetting:
    setting = AdminSetting(**data.model_dump())
    db.add(setting)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "Could not create admin setting due to a data integrity conflict"
        ) from exc
    db.refresh(setting)
    return setting


def get_admin_setting(db: Session, setting_id: int) -> AdminSetting | None:
    return db.get(AdminSetting, setting_id)


def get_admin_settings(db: Session, skip: int = 0, limit: int = 100) -> list[AdminSetting]:
    stmt = select(AdminSetting).order_by(AdminSetting.id).offset(skip).limit(limit)
    return list(db.scalars(stmt).all())


def get_active_admin_settings(db: Session) -> list[AdminSetting]:
    stmt = select(AdminSetting).where(AdminSetting.is_active.is_(True)).order_by(AdminSetting.id)
    return list(db.scalars(stmt).all())


def update_admin_setting(
    db: Session, setting_id: int, data: AdminSettingUpdate
) -> AdminSetting | None:
    setting = get_admin_setting(db, setting_id)
    if setting is None:
        return None
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(setting, field, value)
    # budget_min/budget_max can each be patched independently, so the
    # invariant is only checkable against the merged (post-setattr) state,
    # not just the fields present in this PATCH payload.
    if setting.budget_min > setting.budget_max:
        db.rollback()
        raise DomainValidationError("budget_min must be less than or equal to budget_max")
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "Could not update admin setting due to a data integrity conflict"
        ) from exc
    db.refresh(setting)
    return setting


def delete_admin_setting(db: Session, setting_id: int) -> bool:
    setting = get_admin_setting(db, setting_id)
    if setting is None:
        return False
    db.delete(setting)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "Could not delete admin setting due to a data integrity conflict"
        ) from exc
    return True
