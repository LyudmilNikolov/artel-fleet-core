from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_driver_scope
from app.database import get_db
from app.models.user import User
from app.schemas.mileage import BulkMileageCreate, MileageCreate, MileageRead
from app.services.driver_scope import DriverScope
from app.services.errors import error_detail
from app.services.mileage_service import MileageService

router = APIRouter(prefix="/mileage", tags=["mileage"])


def _require_vehicles(scope: DriverScope, vehicle_ids) -> None:
    """A driver may only read or submit mileage for their assigned vehicles."""
    if not all(scope.allows_vehicle(v) for v in vehicle_ids):
        raise HTTPException(status_code=403, detail="errors.forbidden")


@router.post("", response_model=MileageRead, status_code=status.HTTP_201_CREATED)
async def add_mileage(
    data: MileageCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    scope: DriverScope = Depends(get_driver_scope),
):
    _require_vehicles(scope, [data.vehicle_id])
    service = MileageService(db)
    try:
        return await service.add_reading(data, user.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=error_detail(e))


@router.post("/bulk", response_model=list[MileageRead], status_code=status.HTTP_201_CREATED)
async def add_bulk_mileage(
    data: BulkMileageCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    scope: DriverScope = Depends(get_driver_scope),
):
    _require_vehicles(scope, [e.vehicle_id for e in data.entries])
    service = MileageService(db)
    try:
        return await service.add_bulk(data.entries, user.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=error_detail(e))


@router.get("/vehicle/{vehicle_id}", response_model=list[MileageRead])
async def get_vehicle_mileage(
    vehicle_id: UUID,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
    scope: DriverScope = Depends(get_driver_scope),
):
    _require_vehicles(scope, [vehicle_id])
    service = MileageService(db)
    return await service.get_history(vehicle_id, limit=limit)
