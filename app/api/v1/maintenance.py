from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_fleet_manager
from app.database import get_db
from app.models.maintenance import MaintenanceStatus
from app.models.user import User
from app.repositories.base import BaseRepository
from app.schemas.common import PaginatedResponse
from app.schemas.maintenance import MaintenanceCreate, MaintenanceRead, MaintenanceUpdate, ServiceDueRead
from app.services.errors import DomainError, MileageBelowCurrentError, error_detail
from app.services.maintenance_service import PENDING_STATUSES, MaintenanceService
from app.services.service_due_service import ServiceDueService
from app.vehicle_rules import vehicle_type_filter

router = APIRouter(prefix="/maintenance", tags=["maintenance"])


def _http_error(exc: DomainError) -> HTTPException:
    """404 for unknown records/vehicles, 409 when a lower mileage needs confirmation, else 400."""
    if isinstance(exc, MileageBelowCurrentError):
        return HTTPException(status_code=409, detail=error_detail(exc))
    not_found = exc.key in ("error.maintenance_not_found", "error.vehicle_not_found")
    return HTTPException(status_code=404 if not_found else 400, detail=error_detail(exc))


@router.get("", response_model=PaginatedResponse[MaintenanceRead])
async def list_maintenance(
    status: MaintenanceStatus | Literal["pending"] | None = None,
    when: Literal["overdue", "upcoming"] | None = None,
    q: str | None = None,
    vehicle_type: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """``status=pending`` = scheduled + in progress; ``when`` filters scheduled records by date;
    ``q`` and ``vehicle_type`` (a type, or ``trailer``) filter by vehicle."""
    try:
        types = vehicle_type_filter(vehicle_type)
    except ValueError:
        raise HTTPException(status_code=422, detail=error_detail(DomainError("errors.invalid_input"))) from None
    statuses = list(PENDING_STATUSES) if status == "pending" else [status] if status else []
    service = MaintenanceService(db)
    items, total = await service.list_all(
        status=statuses, q=q, vehicle_types=types, when=when, page=page, size=size
    )
    return PaginatedResponse(
        items=[MaintenanceRead.model_validate(i) for i in items],
        total=total,
        page=page,
        size=size,
        pages=BaseRepository.calc_pages(total, size),
    )


@router.get("/service-due", response_model=list[ServiceDueRead])
async def service_due(
    status: Literal["overdue", "due_soon", "ok", "none"] | None = None,
    vehicle_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Current planned service per vehicle and service item with its due state, most urgent first."""
    views = await ServiceDueService(db).list_due(status, vehicle_id)
    return [
        ServiceDueRead(
            record_id=v.record.id,
            vehicle_id=v.vehicle.id,
            license_plate=v.vehicle.license_plate,
            service_item=v.record.service_item,
            completed_date=v.record.completed_date,
            mileage_at_service=v.record.mileage_at_service,
            next_service_mileage=v.record.next_service_mileage,
            next_service_date=v.record.next_service_date,
            current_mileage=v.vehicle.current_mileage,
            status=v.status,
            km_left=v.due.km_left,
            days_left=v.due.days_left,
        )
        for v in views
    ]


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
    try:
        return await service.create(data, user.id)
    except DomainError as e:
        raise _http_error(e) from e


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
    except DomainError as e:
        raise _http_error(e) from e


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
