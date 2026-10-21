from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mileage import MileageLog
from app.models.vehicle import Vehicle
from app.schemas.mileage import MileageCreate
from app.services.errors import DomainError, MileageBelowCurrentError
from app.vehicle_rules import MILEAGE_TYPES


class MileageService:
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    async def logged_baseline(session: AsyncSession, vehicle_id: UUID) -> int | None:
        """Highest logged reading that still counts: from the latest correction onwards, else overall."""
        query = select(func.max(MileageLog.value)).where(MileageLog.vehicle_id == vehicle_id)
        epoch = await session.scalar(
            select(func.max(MileageLog.seq)).where(MileageLog.vehicle_id == vehicle_id, MileageLog.is_correction)
        )
        if epoch is not None:
            query = query.where(MileageLog.seq >= epoch)
        return await session.scalar(query)

    async def _locked_vehicle(self, vehicle_id: UUID) -> Vehicle:
        vehicle = await self.session.scalar(
            select(Vehicle).where(Vehicle.id == vehicle_id).with_for_update().execution_options(populate_existing=True)
        )
        if not vehicle:
            raise DomainError("error.vehicle_not_found")
        if vehicle.vehicle_type not in MILEAGE_TYPES:
            raise DomainError("error.mileage_unsupported")
        return vehicle

    async def add_reading(self, data: MileageCreate, user_id: UUID) -> MileageLog:
        """Add mileage reading with validation."""
        vehicle = await self._locked_vehicle(data.vehicle_id)
        maximum = await self.logged_baseline(self.session, data.vehicle_id)
        baseline = max(maximum or 0, vehicle.current_mileage or 0)
        if data.value < baseline:
            raise DomainError("error.mileage_decreased", value=data.value, previous=baseline)
        if maximum is not None and data.value == maximum:
            existing = await self.session.scalar(
                select(MileageLog)
                .where(MileageLog.vehicle_id == data.vehicle_id, MileageLog.value == maximum)
                .order_by(MileageLog.seq.desc())
                .limit(1)
            )
            vehicle.current_mileage = maximum
            return existing

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

    async def set_reading(self, data: MileageCreate, user_id: UUID, *, overwrite: bool = False) -> MileageLog:
        """Record a reading that may be lower than the current mileage, but only when confirmed.

        Equal or higher values behave like ``add_reading``. A lower value raises ``MileageBelowCurrentError``
        unless ``overwrite`` is set; then a correction entry is added, becomes the vehicle's current
        mileage and the new baseline. Earlier readings are kept as history.
        """
        vehicle = await self._locked_vehicle(data.vehicle_id)
        logged = await self.logged_baseline(self.session, data.vehicle_id)
        baseline = max(logged or 0, vehicle.current_mileage or 0)
        if data.value >= baseline:
            return await self.add_reading(data, user_id)
        if not overwrite:
            raise MileageBelowCurrentError(value=data.value, previous=baseline)
        log = MileageLog(
            vehicle_id=data.vehicle_id,
            recorded_by=user_id,
            value=data.value,
            source=data.source,
            notes=data.notes,
            is_correction=True,
        )
        self.session.add(log)
        await self.session.flush()
        vehicle.current_mileage = data.value
        await self.session.flush()
        await self.session.refresh(log)
        return log

    async def apply_from_entry(
        self, vehicle_id: UUID, mileage: int | None, note: str | None, user_id: UUID | None, overwrite: bool
    ) -> None:
        """Update the vehicle's mileage from a reading entered on a maintenance or expense record.

        Equal or higher values add a normal reading. A lower value needs ``overwrite`` (raises
        ``MileageBelowCurrentError`` otherwise) and then adds a correction reading. Nothing is written
        before the check passes.
        """
        if mileage is None:
            return
        await self.set_reading(
            MileageCreate(vehicle_id=vehicle_id, value=mileage, notes=note), user_id, overwrite=overwrite
        )

    async def add_bulk(self, entries: list[MileageCreate], user_id: UUID) -> list[MileageLog]:
        results = []
        async with self.session.begin_nested():
            for entry in sorted(entries, key=lambda e: str(e.vehicle_id)):
                log = await self.add_reading(entry, user_id)
                results.append(log)
        return results

    async def get_history(self, vehicle_id: UUID, limit: int = 50) -> list[MileageLog]:
        result = await self.session.execute(
            select(MileageLog)
            .where(MileageLog.vehicle_id == vehicle_id)
            .order_by(MileageLog.recorded_at.desc(), MileageLog.seq.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def _get_latest(self, vehicle_id: UUID) -> MileageLog | None:
        result = await self.session.execute(
            select(MileageLog)
            .where(MileageLog.vehicle_id == vehicle_id)
            .order_by(MileageLog.recorded_at.desc(), MileageLog.seq.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
