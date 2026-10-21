from datetime import date
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.maintenance import MaintenanceRecord, MaintenanceStatus
from app.repositories.base import BaseRepository
from app.schemas.maintenance import MaintenanceCreate, MaintenanceUpdate
from app.services.errors import DomainError


class MaintenanceService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = BaseRepository(MaintenanceRecord, session)

    async def create(self, data: MaintenanceCreate, user_id: UUID) -> MaintenanceRecord:
        return await self.repo.create(**data.model_dump(), created_by=user_id)

    async def update(self, record_id: UUID, data: MaintenanceUpdate) -> MaintenanceRecord:
        record = await self.repo.get_by_id(record_id)
        if not record:
            raise DomainError("error.maintenance_not_found")
        return await self.repo.update(record, **data.model_dump(exclude_unset=True))

    async def complete(self, record_id: UUID) -> MaintenanceRecord:
        record = await self.repo.get_by_id(record_id)
        if not record:
            raise DomainError("error.maintenance_not_found")
        return await self.repo.update(record, status=MaintenanceStatus.COMPLETED, completed_date=date.today())

    async def get_by_id(self, record_id: UUID) -> MaintenanceRecord | None:
        return await self.repo.get_by_id(record_id)

    async def list_for_vehicle(
        self, vehicle_id: UUID, offset: int = 0, limit: int = 50
    ) -> tuple[list[MaintenanceRecord], int]:
        query = select(MaintenanceRecord).where(MaintenanceRecord.vehicle_id == vehicle_id)
        count_q = select(func.count()).select_from(MaintenanceRecord).where(MaintenanceRecord.vehicle_id == vehicle_id)
        total = (await self.session.execute(count_q)).scalar() or 0
        result = await self.session.execute(
            query.order_by(MaintenanceRecord.scheduled_date.desc()).offset(offset).limit(limit)
        )
        return list(result.scalars().all()), total

    async def list_all(
        self, *, status: MaintenanceStatus | None = None, page: int = 1, size: int = 50
    ) -> tuple[list[MaintenanceRecord], int]:
        filters = {}
        if status:
            filters["status"] = status
        return await self.repo.list(offset=(page - 1) * size, limit=size, order_by="-scheduled_date", filters=filters)

    async def get_kanban_data(self) -> dict[str, list[MaintenanceRecord]]:
        result = {}
        for status in [MaintenanceStatus.SCHEDULED, MaintenanceStatus.IN_PROGRESS, MaintenanceStatus.COMPLETED]:
            q = (
                select(MaintenanceRecord)
                .where(MaintenanceRecord.status == status)
                .order_by(MaintenanceRecord.scheduled_date.asc())
                .limit(50)
            )
            res = await self.session.execute(q)
            result[status.value] = list(res.scalars().all())
        return result

    async def delete(self, record_id: UUID) -> None:
        record = await self.repo.get_by_id(record_id)
        if not record:
            raise DomainError("error.maintenance_not_found")
        await self.repo.delete(record)
