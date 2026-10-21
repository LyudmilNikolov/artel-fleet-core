from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_fleet_manager
from app.database import get_db
from app.models.contract import ContractStatus
from app.models.user import User
from app.repositories.base import BaseRepository
from app.schemas.common import PaginatedResponse
from app.schemas.contract import ContractCreate, ContractRead, ContractUpdate
from app.services.contract_service import ContractService
from app.services.errors import error_detail

router = APIRouter(prefix="/contracts", tags=["contracts"])


@router.get("", response_model=PaginatedResponse[ContractRead])
async def list_contracts(
    status: ContractStatus | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    service = ContractService(db)
    items, total = await service.list_all(status=status, page=page, size=size)
    return PaginatedResponse(
        items=[ContractRead.model_validate(i) for i in items],
        total=total,
        page=page,
        size=size,
        pages=BaseRepository.calc_pages(total, size),
    )


@router.get("/{contract_id}", response_model=ContractRead)
async def get_contract(contract_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    service = ContractService(db)
    contract = await service.get_by_id(contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="error.contract_not_found")
    return contract


@router.post("", response_model=ContractRead, status_code=status.HTTP_201_CREATED)
async def create_contract(
    data: ContractCreate, db: AsyncSession = Depends(get_db), user: User = Depends(require_fleet_manager)
):
    service = ContractService(db)
    return await service.create(data, user.id)


@router.patch("/{contract_id}", response_model=ContractRead)
async def update_contract(
    contract_id: UUID,
    data: ContractUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_fleet_manager),
):
    service = ContractService(db)
    try:
        return await service.update(contract_id, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=error_detail(e))


@router.delete("/{contract_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contract(
    contract_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(require_fleet_manager)
):
    service = ContractService(db)
    try:
        await service.delete(contract_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=error_detail(e))
