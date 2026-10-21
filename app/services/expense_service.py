from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.expense import Expense, ExpenseCategory
from app.repositories.base import BaseRepository
from app.schemas.expense import ExpenseCreate, ExpenseUpdate
from app.services.errors import DomainError


class ExpenseService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = BaseRepository(Expense, session)

    async def create(self, data: ExpenseCreate, user_id: UUID) -> Expense:
        data = ExpenseCreate.model_validate(data.model_dump())
        return await self.repo.create(**data.model_dump(), created_by=user_id)

    async def update(self, expense_id: UUID, data: ExpenseUpdate) -> Expense:
        expense = await self.repo.get_by_id(expense_id)
        if not expense:
            raise DomainError("error.expense_not_found")
        data = ExpenseUpdate.model_validate(data.model_dump(exclude_unset=True))
        return await self.repo.update(expense, **data.model_dump(exclude_unset=True))

    async def get_by_id(self, expense_id: UUID) -> Expense | None:
        return await self.repo.get_by_id(expense_id)

    async def list_for_vehicle(self, vehicle_id: UUID, offset: int = 0, limit: int = 50) -> tuple[list[Expense], int]:
        query = select(Expense).where(Expense.vehicle_id == vehicle_id)
        count_q = select(func.count()).select_from(Expense).where(Expense.vehicle_id == vehicle_id)
        total = (await self.session.execute(count_q)).scalar() or 0
        result = await self.session.execute(query.order_by(Expense.date.desc()).offset(offset).limit(limit))
        return list(result.scalars().all()), total

    async def list_all(
        self, *, category: ExpenseCategory | None = None, page: int = 1, size: int = 50
    ) -> tuple[list[Expense], int]:
        filters = {}
        if category:
            filters["category"] = category
        return await self.repo.list(offset=(page - 1) * size, limit=size, order_by="-date", filters=filters)

    async def cost_breakdown_by_category(self, vehicle_id: UUID | None = None) -> dict[str, Decimal]:
        query = select(Expense.category, func.sum(Expense.amount))
        if vehicle_id:
            query = query.where(Expense.vehicle_id == vehicle_id)
        query = query.group_by(Expense.category)
        result = await self.session.execute(query)
        return {row[0].value: Decimal(row[1]) for row in result.all()}

    async def delete(self, expense_id: UUID) -> None:
        expense = await self.repo.get_by_id(expense_id)
        if not expense:
            raise DomainError("error.expense_not_found")
        await self.repo.delete(expense)
