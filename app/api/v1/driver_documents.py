"""REST API for per-driver tracked documents (Лицензи и документи)."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_fleet_manager
from app.database import get_db
from app.driver_document_rules import DRIVER_DOCUMENT_TYPES, mask_number
from app.i18n import _
from app.models.user import User, UserRole
from app.repositories.base import BaseRepository
from app.repositories.driver_repo import DriverRepository
from app.schemas.common import PaginatedResponse
from app.schemas.driver_document import (
    DriverDocumentCreate,
    DriverDocumentRead,
    DriverDocumentRenew,
    DriverDocumentTypeOption,
    DriverDocumentUpdate,
)
from app.services.driver_access import can_read_driver_documents, can_see_numbers
from app.services.driver_document_service import DriverDocumentService, DriverDocumentView
from app.services.errors import DomainError, error_detail

router = APIRouter(prefix="/driver-documents", tags=["driver-documents"])

NOT_FOUND_KEYS = ("error.driver_document_not_found", "error.driver_not_found")


def _bad_request(exc: DomainError) -> HTTPException:
    return HTTPException(status_code=404 if exc.key in NOT_FOUND_KEYS else 400, detail=error_detail(exc))


def _read(view: DriverDocumentView, user: User) -> DriverDocumentRead:
    """Serialize an entry; numbers are masked for roles that may not see them in full."""
    item = DriverDocumentRead.model_validate(view)
    if not can_see_numbers(user, view.driver):
        item.document_number = mask_number(item.document_number)
    return item


def _require_read(user: User, view: DriverDocumentView) -> None:
    if not can_read_driver_documents(user, view.driver):
        raise HTTPException(status_code=403, detail="errors.forbidden")


@router.get("", response_model=PaginatedResponse[DriverDocumentRead])
async def list_driver_documents(
    driver_id: UUID | None = None,
    document_type: str | None = None,
    status: Literal["valid", "expiring_soon", "expired", "no_expiry"] | None = None,
    include_history: bool = False,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.role == UserRole.DRIVER:
        own = await DriverRepository(db).get_for_user(user.id)
        if own is None or (driver_id is not None and driver_id != own.id):
            raise HTTPException(status_code=403, detail="errors.forbidden")
        driver_id = own.id
    try:
        items, total = await DriverDocumentService(db).list_entries(
            driver_id=driver_id,
            document_type=document_type,
            status=status,
            current_only=not include_history,
            page=page,
            size=size,
        )
    except DomainError as exc:
        raise _bad_request(exc) from exc
    return PaginatedResponse(
        items=[_read(i, user) for i in items],
        total=total,
        page=page,
        size=size,
        pages=BaseRepository.calc_pages(total, size),
    )


@router.get("/types", response_model=list[DriverDocumentTypeOption])
async def driver_document_types(request: Request, _user: User = Depends(get_current_user)):
    lang = request.state.locale
    return [
        DriverDocumentTypeOption(
            code=code,
            label=_("driver_document_type." + code, lang=lang),
            number_required=spec.number_required,
            has_categories=spec.has_categories,
        )
        for code, spec in DRIVER_DOCUMENT_TYPES.items()
    ]


@router.get("/{entry_id}", response_model=DriverDocumentRead)
async def get_driver_document(
    entry_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    view = await DriverDocumentService(db).get(entry_id)
    if not view:
        raise HTTPException(status_code=404, detail="error.driver_document_not_found")
    _require_read(user, view)
    return _read(view, user)


@router.post("", response_model=DriverDocumentRead, status_code=status.HTTP_201_CREATED)
async def create_driver_document(
    data: DriverDocumentCreate, db: AsyncSession = Depends(get_db), user: User = Depends(require_fleet_manager)
):
    try:
        return _read(await DriverDocumentService(db).create(data, user.id), user)
    except DomainError as exc:
        raise _bad_request(exc) from exc


@router.post("/{entry_id}/renew", response_model=DriverDocumentRead, status_code=status.HTTP_201_CREATED)
async def renew_driver_document(
    entry_id: UUID,
    data: DriverDocumentRenew,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_fleet_manager),
):
    try:
        return _read(await DriverDocumentService(db).renew(entry_id, data, user.id), user)
    except DomainError as exc:
        raise _bad_request(exc) from exc


@router.patch("/{entry_id}", response_model=DriverDocumentRead)
async def update_driver_document(
    entry_id: UUID,
    data: DriverDocumentUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_fleet_manager),
):
    try:
        return _read(await DriverDocumentService(db).update(entry_id, data, user.id), user)
    except DomainError as exc:
        raise _bad_request(exc) from exc


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_driver_document(
    entry_id: UUID,
    scope: Literal["entry", "all"] = "entry",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_fleet_manager),
):
    """Delete one entry, or with ``scope=all`` every entry of the same driver and type."""
    try:
        await DriverDocumentService(db).delete(entry_id, scope, user.id)
    except DomainError as exc:
        raise _bad_request(exc) from exc
