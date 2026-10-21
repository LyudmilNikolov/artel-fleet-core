from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.mileage import BulkMileageCreate, MileageCreate, MileageRead
from app.services.errors import error_detail
from app.services.mileage_service import MileageService

router = APIRouter(prefix="/mileage", tags=["mileage"])


@router.post("", response_model=MileageRead, status_code=status.HTTP_201_CREATED)
async def add_mileage(
    data: MileageCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
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
):
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
):
    service = MileageService(db)
    return await service.get_history(vehicle_id, limit=limit)
