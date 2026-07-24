"""HTTP routes for the Application entity: validate via schemas, delegate logic to crud."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_admin
from app.core.exceptions import ConflictError
from app.crud import application as crud
from app.schemas.application import ApplicationCreate, ApplicationRead, ApplicationUpdate

router = APIRouter(prefix="/applications", tags=["applications"])


@router.post("", response_model=ApplicationRead, status_code=status.HTTP_201_CREATED)
def create_application(payload: ApplicationCreate, db: Session = Depends(get_db)) -> ApplicationRead:
    # Public: this is the client-facing application form's submit endpoint.
    try:
        return crud.create_application(db, payload)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("", response_model=list[ApplicationRead], dependencies=[Depends(get_current_admin)])
def list_applications(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[ApplicationRead]:
    return crud.get_applications(db, skip=skip, limit=limit)


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
