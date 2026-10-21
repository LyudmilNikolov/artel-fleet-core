from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mileage import MileageLog
from app.models.vehicle import Vehicle
from app.schemas.mileage import MileageCreate
from app.services.errors import DomainError


class MileageService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add_reading(self, data: MileageCreate, user_id: UUID) -> MileageLog:
        """Add mileage reading with validation."""
        vehicle = await self.session.get(Vehicle, data.vehicle_id)
        if not vehicle:
            raise DomainError("error.vehicle_not_found")

        # Validate >= previous reading
        latest = await self._get_latest(data.vehicle_id)
        if latest and data.value < latest.value:
            raise DomainError("error.mileage_decreased", value=data.value, previous=latest.value)

        # Check for abnormal jump (> 1000 km/day)
        if latest and data.value - latest.value > 1000:
            pass  # Could emit warning/notification; allowed but flagged

        log = MileageLog(
            vehicle_id=data.vehicle_id,
            recorded_by=user_id,
            value=data.value,
            source=data.source,
            notes=data.notes,
        )
        self.session.add(log)
        await self.session.flush()

        # Auto-update vehicle mileage
        vehicle.current_mileage = data.value
        await self.session.flush()
        await self.session.refresh(log)
        return log

    async def add_bulk(self, entries: list[MileageCreate], user_id: UUID) -> list[MileageLog]:
        results = []
        for entry in entries:
            log = await self.add_reading(entry, user_id)
            results.append(log)
        return results

    async def get_history(self, vehicle_id: UUID, limit: int = 50) -> list[MileageLog]:
        result = await self.session.execute(
            select(MileageLog)
            .where(MileageLog.vehicle_id == vehicle_id)
            .order_by(MileageLog.recorded_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def _get_latest(self, vehicle_id: UUID) -> MileageLog | None:
        result = await self.session.execute(
            select(MileageLog)
            .where(MileageLog.vehicle_id == vehicle_id)
            .order_by(MileageLog.recorded_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
