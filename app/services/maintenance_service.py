from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, EntityType
from app.models.maintenance import MaintenanceRecord, MaintenanceStatus
from app.models.vehicle import Vehicle
from app.repositories.base import BaseRepository
from app.schemas.maintenance import MaintenanceCreate, MaintenanceUpdate
from app.services.errors import DomainError
from app.services.mileage_service import MileageService
from app.utils.s3 import get_s3_client


def _today() -> date:
    """Today's date in UTC (all dates are UTC by convention)."""
    return datetime.now(UTC).date()


def _with_completion(values: dict, current_status=None, current_completed=None) -> dict:
    """Keep status and completed_date consistent.

    A completed record always has a completed date (given, kept, or today); any other status has none.
    """
    status = values.get("status", current_status)
    if status == MaintenanceStatus.COMPLETED:
        values["completed_date"] = values.get("completed_date") or current_completed or _today()
    else:
        values["completed_date"] = None
    return values


class MaintenanceService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = BaseRepository(MaintenanceRecord, session)

    async def create(self, data: MaintenanceCreate, user_id: UUID) -> MaintenanceRecord:
        """Create a record for an existing vehicle; a record created as completed gets its completed date."""
        vehicle = await self.session.get(Vehicle, data.vehicle_id)
        if vehicle is None:
            raise DomainError("error.vehicle_not_found")
        values = _with_completion(data.model_dump(exclude={"overwrite_mileage"}))
        await MileageService(self.session).apply_from_entry(
            vehicle.id, values["mileage_at_service"], values["title"], user_id, data.overwrite_mileage
        )
        return await self.repo.create(**values, created_by=user_id)

    async def update(self, record_id: UUID, data: MaintenanceUpdate) -> MaintenanceRecord:
        """Change a record; moving it to completed sets the completed date, moving it away clears it."""
        record = await self.repo.get_by_id(record_id)
        if not record:
            raise DomainError("error.maintenance_not_found")
        values = data.model_dump(exclude_unset=True, exclude={"overwrite_mileage"})
        if "status" in values or "completed_date" in values:
            values = _with_completion(values, record.status, record.completed_date)
        mileage = values.get("mileage_at_service")
        if mileage is not None and mileage != record.mileage_at_service:
            await MileageService(self.session).apply_from_entry(
                record.vehicle_id, mileage, values.get("title", record.title), None, data.overwrite_mileage
            )
        return await self.repo.update(record, **values)

    async def complete(self, record_id: UUID, completed_date: date | None = None) -> MaintenanceRecord:
        """Mark a record completed (today unless a date is given)."""
        record = await self.repo.get_by_id(record_id)
        if not record:
            raise DomainError("error.maintenance_not_found")
        return await self.repo.update(
            record, status=MaintenanceStatus.COMPLETED, completed_date=completed_date or _today()
        )

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
        """Delete a record together with its attachments and their stored files."""
        record = await self.repo.get_by_id(record_id)
        if not record:
            raise DomainError("error.maintenance_not_found")
        documents = (
            await self.session.scalars(
                select(Document).where(Document.entity_type == EntityType.MAINTENANCE, Document.entity_id == record.id)
            )
        ).all()
        keys = [doc.s3_key for doc in documents]
        for doc in documents:
            await self.session.delete(doc)
        await self.repo.delete(record)
        if keys:
            s3 = get_s3_client()
            for key in keys:
                try:
                    s3.delete_file(key)
                except Exception:  # noqa: BLE001 - rows are already gone; do not fail the delete
                    pass
