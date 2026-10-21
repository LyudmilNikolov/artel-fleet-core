from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, EntityType
from app.models.expense import Expense, ExpenseCategory
from app.models.vehicle import Vehicle
from app.repositories.base import BaseRepository
from app.schemas.expense import ExpenseCreate, ExpenseUpdate
from app.services import document_expense_sync as sync
from app.services.errors import DomainError
from app.services.mileage_service import MileageService
from app.utils.s3 import get_s3_client
from app.vehicle_rules import validate_expense_category


class ExpenseService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = BaseRepository(Expense, session)

    @staticmethod
    def _check_category(vehicle: Vehicle, category) -> None:
        """New categories must still be offered and apply to the vehicle type."""
        try:
            validate_expense_category(vehicle.vehicle_type, category)
        except ValueError as exc:
            raise DomainError(str(exc)) from exc

    async def create(self, data: ExpenseCreate, user_id: UUID) -> Expense:
        """Create an expense; in a document category with validity dates, also its linked document.

        A mileage on a tracked vehicle updates the vehicle (a lower one needs ``overwrite_mileage``).
        Every check runs before anything is written.
        """
        data = ExpenseCreate.model_validate(data.model_dump())
        vehicle = await self.session.get(Vehicle, data.vehicle_id)
        if vehicle is None:
            raise DomainError("error.vehicle_not_found")
        self._check_category(vehicle, data.category)
        has_dates = (data.valid_from is not None) + (data.valid_until is not None)
        wants_document = False
        if has_dates or data.policy_number:
            if not sync.is_document_category(data.category):
                raise DomainError("error.expense_validity_not_applicable")
            if has_dates == 1:
                raise DomainError("error.expense_document_dates_required")
            wants_document = has_dates == 2
            if wants_document:
                if data.valid_until < data.valid_from:
                    raise DomainError("error.compliance_end_before_start")
                await sync.require_applicable(self.session, data.vehicle_id, data.category)
        await MileageService(self.session).apply_from_entry(
            data.vehicle_id, data.mileage_at_refuel, data.description, user_id, data.overwrite_mileage
        )
        values = data.model_dump(exclude={"valid_from", "valid_until", "policy_number", "overwrite_mileage"})
        expense = await self.repo.create(**values, created_by=user_id)
        if wants_document:
            await sync.create_document_for_expense(
                self.session,
                expense,
                valid_from=data.valid_from,
                valid_until=data.valid_until,
                policy_number=data.policy_number,
                user_id=user_id,
            )
        return expense

    async def update(self, expense_id: UUID, data: ExpenseUpdate) -> Expense:
        """Change an expense; amount and vendor flow into its linked document. A linked expense keeps its category."""
        expense = await self.repo.get_by_id(expense_id)
        if not expense:
            raise DomainError("error.expense_not_found")
        data = ExpenseUpdate.model_validate(data.model_dump(exclude_unset=True))
        values = data.model_dump(exclude_unset=True, exclude={"overwrite_mileage"})
        linked = expense.compliance_document is not None
        if linked and values.get("category", expense.category) != expense.category:
            raise DomainError("error.expense_category_locked")
        if values.get("category", expense.category) != expense.category:
            vehicle = await self.session.get(Vehicle, expense.vehicle_id)
            self._check_category(vehicle, values["category"])
        mileage = values.get("mileage_at_refuel")
        if mileage is not None and mileage != expense.mileage_at_refuel:
            await MileageService(self.session).apply_from_entry(
                expense.vehicle_id,
                mileage,
                values.get("description", expense.description),
                None,
                data.overwrite_mileage,
            )
        expense = await self.repo.update(expense, **values)
        if linked and ("amount" in values or "vendor" in values):
            await sync.sync_from_expense(self.session, expense)
        return expense

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
        """Delete an expense with its attachments. A linked document stays but loses its cost."""
        expense = await self.repo.get_by_id(expense_id)
        if not expense:
            raise DomainError("error.expense_not_found")
        await sync.release_document(self.session, expense)
        documents = (
            await self.session.scalars(
                select(Document).where(Document.entity_type == EntityType.EXPENSE, Document.entity_id == expense.id)
            )
        ).all()
        keys = [doc.s3_key for doc in documents]
        for doc in documents:
            await self.session.delete(doc)
        await self.repo.delete(expense)
        if keys:
            s3 = get_s3_client()
            for key in keys:
                try:
                    s3.delete_file(key)
                except Exception:  # noqa: BLE001 - rows are already gone; do not fail the delete
                    pass
