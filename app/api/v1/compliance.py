"""REST API for per-vehicle compliance documents (Документи и срокове)."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_fleet_manager
from app.database import get_db
from app.i18n import _
from app.models.user import User
from app.repositories.base import BaseRepository
from app.schemas.common import PaginatedResponse
from app.schemas.compliance import (
    ComplianceCreate,
    ComplianceRead,
    ComplianceRenew,
    ComplianceTypeOption,
    ComplianceUpdate,
)
from app.services.compliance_service import ComplianceService
from app.services.errors import DomainError, error_detail

router = APIRouter(prefix="/compliance-documents", tags=["compliance-documents"])


def _bad_request(exc: DomainError) -> HTTPException:
    not_found = exc.key in ("error.compliance_not_found", "error.compliance_vehicle_not_found")
    return HTTPException(status_code=404 if not_found else 400, detail=error_detail(exc))


@router.get("", response_model=PaginatedResponse[ComplianceRead])
async def list_compliance_documents(
    vehicle_id: UUID | None = None,
    q: str | None = None,
    document_type: str | None = None,
    status: Literal["valid", "expiring_soon", "expired"] | None = None,
    include_history: bool = False,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    items, total = await ComplianceService(db).list_entries(
        vehicle_id=vehicle_id,
        q=q,
        document_type=document_type,
        status=status,
        current_only=not include_history,
        page=page,
        size=size,
    )
    return PaginatedResponse(
        items=[ComplianceRead.model_validate(i) for i in items],
        total=total,
        page=page,
        size=size,
        pages=BaseRepository.calc_pages(total, size),
    )


@router.get("/types", response_model=list[ComplianceTypeOption])
async def compliance_types(
    request: Request,
    vehicle_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    try:
        codes = await ComplianceService(db).allowed_types(vehicle_id)
    except DomainError as exc:
        raise _bad_request(exc) from exc
    lang = request.state.locale
    return [ComplianceTypeOption(code=c, label=_("compliance_type." + c, lang=lang)) for c in codes]


@router.get("/{entry_id}", response_model=ComplianceRead)
async def get_compliance_document(
    entry_id: UUID, db: AsyncSession = Depends(get_db), _user: User = Depends(get_current_user)
):
    view = await ComplianceService(db).get(entry_id)
    if not view:
        raise HTTPException(status_code=404, detail="error.compliance_not_found")
    return ComplianceRead.model_validate(view)


@router.post("", response_model=ComplianceRead, status_code=status.HTTP_201_CREATED)
async def create_compliance_document(
    data: ComplianceCreate, db: AsyncSession = Depends(get_db), user: User = Depends(require_fleet_manager)
):
    try:
        return ComplianceRead.model_validate(await ComplianceService(db).create(data, user.id))
    except DomainError as exc:
        raise _bad_request(exc) from exc


@router.post("/{entry_id}/renew", response_model=ComplianceRead, status_code=status.HTTP_201_CREATED)
async def renew_compliance_document(
    entry_id: UUID,
    data: ComplianceRenew,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_fleet_manager),
):
    try:
        return ComplianceRead.model_validate(await ComplianceService(db).renew(entry_id, data, user.id))
    except DomainError as exc:
        raise _bad_request(exc) from exc


@router.patch("/{entry_id}", response_model=ComplianceRead)
async def update_compliance_document(
    entry_id: UUID,
    data: ComplianceUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_fleet_manager),
):
    try:
        return ComplianceRead.model_validate(await ComplianceService(db).update(entry_id, data))
    except DomainError as exc:
        raise _bad_request(exc) from exc


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_compliance_document(
    entry_id: UUID,
    scope: Literal["entry", "all"] = "entry",
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_fleet_manager),
):
    """Delete one entry, or with ``scope=all`` every entry of the same vehicle and type."""
    try:
        await ComplianceService(db).delete(entry_id, scope)
    except DomainError as exc:
        raise _bad_request(exc) from exc
