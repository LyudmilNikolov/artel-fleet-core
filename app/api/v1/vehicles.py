from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_fleet_manager
from app.database import get_db
from app.models.user import User
from app.models.vehicle import BodyType, FuelType, VehicleStatus
from app.repositories.base import BaseRepository
from app.schemas.common import PaginatedResponse
from app.schemas.vehicle import VehicleCreate, VehicleRead, VehicleUpdate
from app.services.errors import error_detail
from app.services.vehicle_service import VehicleService

router = APIRouter(prefix="/vehicles", tags=["vehicles"])


@router.get("", response_model=PaginatedResponse[VehicleRead])
async def list_vehicles(
    q: str | None = None,
    status: VehicleStatus | None = None,
    brand: str | None = None,
    fuel_type: FuelType | None = None,
    body_type: BodyType | None = None,
    department: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    service = VehicleService(db)
    items, total = await service.list_vehicles(
        q=q,
        status=status,
        brand=brand,
        fuel_type=fuel_type,
        body_type=body_type,
        department=department,
        page=page,
        size=size,
    )
    return PaginatedResponse(
        items=[VehicleRead.model_validate(v) for v in items],
        total=total,
        page=page,
        size=size,
        pages=BaseRepository.calc_pages(total, size),
    )


@router.get("/{vehicle_id}", response_model=VehicleRead)
async def get_vehicle(
    vehicle_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    service = VehicleService(db)
    vehicle = await service.get_by_id(vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=404, detail="error.vehicle_not_found")
    return vehicle


@router.post("", response_model=VehicleRead, status_code=status.HTTP_201_CREATED)
async def create_vehicle(
    data: VehicleCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_fleet_manager),
):
    service = VehicleService(db)
    try:
        return await service.create(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=error_detail(e))


@router.patch("/{vehicle_id}", response_model=VehicleRead)
async def update_vehicle(
    vehicle_id: UUID,
    data: VehicleUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_fleet_manager),
):
    service = VehicleService(db)
    try:
        return await service.update(vehicle_id, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=error_detail(e))


@router.delete("/{vehicle_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_vehicle(
    vehicle_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_fleet_manager),
):
    service = VehicleService(db)
    try:
        await service.delete(vehicle_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=error_detail(e))
