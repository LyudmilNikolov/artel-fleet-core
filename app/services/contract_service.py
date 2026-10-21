from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contract import Contract, ContractStatus
from app.repositories.base import BaseRepository
from app.schemas.contract import ContractCreate, ContractUpdate
from app.services.errors import DomainError


class ContractService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = BaseRepository(Contract, session)

    async def create(self, data: ContractCreate, user_id: UUID) -> Contract:
        return await self.repo.create(**data.model_dump(), created_by=user_id)

    async def update(self, contract_id: UUID, data: ContractUpdate) -> Contract:
        contract = await self.repo.get_by_id(contract_id)
        if not contract:
            raise DomainError("error.contract_not_found")
        return await self.repo.update(contract, **data.model_dump(exclude_unset=True))

    async def get_by_id(self, contract_id: UUID) -> Contract | None:
        return await self.repo.get_by_id(contract_id)

    async def list_for_vehicle(self, vehicle_id: UUID, offset: int = 0, limit: int = 50) -> tuple[list[Contract], int]:
        query = select(Contract).where(Contract.vehicle_id == vehicle_id)
        count_q = select(func.count()).select_from(Contract).where(Contract.vehicle_id == vehicle_id)
        total = (await self.session.execute(count_q)).scalar() or 0
        result = await self.session.execute(query.order_by(Contract.end_date.desc()).offset(offset).limit(limit))
        return list(result.scalars().all()), total

    async def list_all(
        self, *, status: ContractStatus | None = None, page: int = 1, size: int = 50
    ) -> tuple[list[Contract], int]:
        filters = {}
        if status:
            filters["status"] = status
        return await self.repo.list(offset=(page - 1) * size, limit=size, order_by="-end_date", filters=filters)

    async def delete(self, contract_id: UUID) -> None:
        contract = await self.repo.get_by_id(contract_id)
        if not contract:
            raise DomainError("error.contract_not_found")
        await self.repo.delete(contract)
