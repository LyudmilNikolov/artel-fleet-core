from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_fleet_manager
from app.database import get_db
from app.models.maintenance import MaintenanceStatus
from app.models.user import User
from app.repositories.base import BaseRepository
from app.schemas.common import PaginatedResponse
from app.schemas.maintenance import MaintenanceCreate, MaintenanceRead, MaintenanceUpdate
from app.services.errors import error_detail
from app.services.maintenance_service import MaintenanceService

router = APIRouter(prefix="/maintenance", tags=["maintenance"])


@router.get("", response_model=PaginatedResponse[MaintenanceRead])
async def list_maintenance(
    status: MaintenanceStatus | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    service = MaintenanceService(db)
    items, total = await service.list_all(status=status, page=page, size=size)
    return PaginatedResponse(
        items=[MaintenanceRead.model_validate(i) for i in items],
        total=total,
        page=page,
        size=size,
        pages=BaseRepository.calc_pages(total, size),
    )


@router.get("/{record_id}", response_model=MaintenanceRead)
async def get_maintenance(record_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    service = MaintenanceService(db)
    record = await service.get_by_id(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="error.maintenance_not_found")
    return record


@router.post("", response_model=MaintenanceRead, status_code=status.HTTP_201_CREATED)
async def create_maintenance(
    data: MaintenanceCreate, db: AsyncSession = Depends(get_db), user: User = Depends(require_fleet_manager)
):
    service = MaintenanceService(db)
    return await service.create(data, user.id)


@router.patch("/{record_id}", response_model=MaintenanceRead)
async def update_maintenance(
    record_id: UUID,
    data: MaintenanceUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_fleet_manager),
):
    service = MaintenanceService(db)
    try:
        return await service.update(record_id, data)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=error_detail(e))


@router.patch("/{record_id}/complete", response_model=MaintenanceRead)
async def complete_maintenance(
    record_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(require_fleet_manager)
):
    service = MaintenanceService(db)
    try:
        return await service.complete(record_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=error_detail(e))


@router.delete("/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_maintenance(
    record_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(require_fleet_manager)
):
    service = MaintenanceService(db)
    try:
        await service.delete(record_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=error_detail(e))
