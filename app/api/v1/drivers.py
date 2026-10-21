from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_fleet_manager
from app.database import get_db
from app.models.driver import DriverStatus
from app.models.user import User
from app.repositories.base import BaseRepository
from app.schemas.common import PaginatedResponse
from app.schemas.driver import DriverCreate, DriverDocumentAlerts, DriverRead, DriverUpdate
from app.services.driver_document_service import DriverDocumentService
from app.services.driver_service import DriverService
from app.services.errors import error_detail

router = APIRouter(prefix="/drivers", tags=["drivers"])


@router.get("", response_model=PaginatedResponse[DriverRead])
async def list_drivers(
    q: str | None = None,
    status: DriverStatus | None = None,
    doc_status: Literal["expired", "expiring_soon"] | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    service = DriverService(db)
    items, total = await service.list_drivers(q=q, status=status, doc_status=doc_status, page=page, size=size)
    alerts = await DriverDocumentService(db).alert_counts_by_driver([d.id for d in items])
    return PaginatedResponse(
        items=[_with_alerts(d, alerts) for d in items],
        total=total,
        page=page,
        size=size,
        pages=BaseRepository.calc_pages(total, size),
    )


def _with_alerts(driver, alerts: dict) -> DriverRead:
    item = DriverRead.model_validate(driver)
    item.document_alerts = DriverDocumentAlerts(**alerts.get(driver.id, {}))
    return item


@router.get("/{driver_id}", response_model=DriverRead)
async def get_driver(driver_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    service = DriverService(db)
    driver = await service.get_by_id(driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="error.driver_not_found")
    alerts = await DriverDocumentService(db).alert_counts_by_driver([driver.id])
    return _with_alerts(driver, alerts)


@router.post("", response_model=DriverRead, status_code=status.HTTP_201_CREATED)
async def create_driver(
    data: DriverCreate, db: AsyncSession = Depends(get_db), _: User = Depends(require_fleet_manager)
):
    service = DriverService(db)
    return await service.create(data)


@router.patch("/{driver_id}", response_model=DriverRead)
async def update_driver(
    driver_id: UUID, data: DriverUpdate, db: AsyncSession = Depends(get_db), _: User = Depends(require_fleet_manager)
):
    service = DriverService(db)
    try:
        return await service.update(driver_id, data)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=error_detail(e))


@router.delete("/{driver_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_driver(driver_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(require_fleet_manager)):
    service = DriverService(db)
    try:
        await service.delete(driver_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=error_detail(e))
