from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_fleet_manager
from app.database import get_db
from app.models.expense import ExpenseCategory
from app.models.user import User
from app.repositories.base import BaseRepository
from app.schemas.common import PaginatedResponse
from app.schemas.expense import ExpenseCreate, ExpenseRead, ExpenseUpdate
from app.services.errors import DomainError, MileageBelowCurrentError, error_detail
from app.services.expense_service import ExpenseService

router = APIRouter(prefix="/expenses", tags=["expenses"])


def _http_error(exc: DomainError) -> HTTPException:
    """404 for unknown records/vehicles, 409 when a lower mileage needs confirmation, else 400."""
    if isinstance(exc, MileageBelowCurrentError):
        return HTTPException(status_code=409, detail=error_detail(exc))
    not_found = exc.key in ("error.expense_not_found", "error.vehicle_not_found")
    return HTTPException(status_code=404 if not_found else 400, detail=error_detail(exc))


@router.get("", response_model=PaginatedResponse[ExpenseRead])
async def list_expenses(
    category: ExpenseCategory | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    service = ExpenseService(db)
    items, total = await service.list_all(category=category, page=page, size=size)
    return PaginatedResponse(
        items=[ExpenseRead.model_validate(i) for i in items],
        total=total,
        page=page,
        size=size,
        pages=BaseRepository.calc_pages(total, size),
    )


@router.get("/{expense_id}", response_model=ExpenseRead)
async def get_expense(expense_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    service = ExpenseService(db)
    expense = await service.get_by_id(expense_id)
    if not expense:
        raise HTTPException(status_code=404, detail="error.expense_not_found")
    return expense


@router.post("", response_model=ExpenseRead, status_code=status.HTTP_201_CREATED)
async def create_expense(
    data: ExpenseCreate, db: AsyncSession = Depends(get_db), user: User = Depends(require_fleet_manager)
):
    service = ExpenseService(db)
    try:
        return await service.create(data, user.id)
    except DomainError as e:
        raise _http_error(e) from e


@router.patch("/{expense_id}", response_model=ExpenseRead)
async def update_expense(
    expense_id: UUID, data: ExpenseUpdate, db: AsyncSession = Depends(get_db), _: User = Depends(require_fleet_manager)
):
    service = ExpenseService(db)
    try:
        return await service.update(expense_id, data)
    except DomainError as e:
        raise _http_error(e) from e


@router.delete("/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_expense(
    expense_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(require_fleet_manager)
):
    service = ExpenseService(db)
    try:
        await service.delete(expense_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=error_detail(e))
