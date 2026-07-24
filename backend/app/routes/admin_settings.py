"""HTTP routes for the AdminSetting entity: validate via schemas, delegate logic to crud."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import ConflictError, DomainValidationError
from app.crud import admin_setting as crud
from app.schemas.admin_setting import AdminSettingCreate, AdminSettingRead, AdminSettingUpdate

router = APIRouter(prefix="/admin-settings", tags=["admin-settings"])


@router.post("", response_model=AdminSettingRead, status_code=status.HTTP_201_CREATED)
def create_admin_setting(
    payload: AdminSettingCreate, db: Session = Depends(get_db)
) -> AdminSettingRead:
    try:
        return crud.create_admin_setting(db, payload)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("", response_model=list[AdminSettingRead])
def list_admin_settings(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[AdminSettingRead]:
    return crud.get_admin_settings(db, skip=skip, limit=limit)


@router.get("/active", response_model=list[AdminSettingRead])
def list_active_admin_settings(db: Session = Depends(get_db)) -> list[AdminSettingRead]:
    """Active services only — used by the frontend to populate the service dropdown."""
    return crud.get_active_admin_settings(db)


@router.get("/{setting_id}", response_model=AdminSettingRead)
def get_admin_setting(setting_id: int, db: Session = Depends(get_db)) -> AdminSettingRead:
    setting = crud.get_admin_setting(db, setting_id)
    if setting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Admin setting not found")
    return setting


@router.patch("/{setting_id}", response_model=AdminSettingRead)
def update_admin_setting(
    setting_id: int, payload: AdminSettingUpdate, db: Session = Depends(get_db)
) -> AdminSettingRead:
    try:
        setting = crud.update_admin_setting(db, setting_id, payload)
    except DomainValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if setting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Admin setting not found")
    return setting


@router.delete("/{setting_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_admin_setting(setting_id: int, db: Session = Depends(get_db)) -> None:
    try:
        deleted = crud.delete_admin_setting(db, setting_id)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Admin setting not found")
