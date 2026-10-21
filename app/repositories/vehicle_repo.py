import re
from collections.abc import Sequence

from sqlalchemy import false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vehicle import Vehicle, VehicleStatus
from app.repositories.base import BaseRepository


class VehicleRepository(BaseRepository[Vehicle]):
    def __init__(self, session: AsyncSession):
        super().__init__(Vehicle, session)

    async def search(
        self,
        *,
        q: str | None = None,
        status: VehicleStatus | None = None,
        brand: str | None = None,
        fuel_type: str | None = None,
        vehicle_type: str | Sequence[str] | None = None,
        department: str | None = None,
        offset: int = 0,
        limit: int = 50,
        order_by: str = "-created_at",
    ) -> tuple[list[Vehicle], int]:
        query = select(Vehicle)
        count_query = select(func.count()).select_from(Vehicle)

        if q and q.strip():
            q = q.strip()
            pattern = f"%{q}%"
            # Plates are typed with or without spaces/dashes ("CA1234AB" finds "CA 1234 AB").
            compact = re.sub(r"[\s-]", "", q)
            plate = func.replace(func.replace(Vehicle.license_plate, " ", ""), "-", "")
            filt = or_(
                Vehicle.license_plate.ilike(pattern),
                plate.ilike(f"%{compact}%") if compact else false(),
                Vehicle.vin.ilike(pattern),
                Vehicle.brand.ilike(pattern),
                Vehicle.model.ilike(pattern),
            )
            query = query.where(filt)
            count_query = count_query.where(filt)

        for attr, val in [
            ("status", status),
            ("brand", brand),
            ("fuel_type", fuel_type),
            ("vehicle_type", vehicle_type),
            ("department", department),
        ]:
            if val is not None:
                col = getattr(Vehicle, attr)
                condition = col.in_(list(val)) if isinstance(val, (list, tuple, set, frozenset)) else col == val
                query = query.where(condition)
                count_query = count_query.where(condition)

        total = (await self.session.execute(count_query)).scalar() or 0

        desc = order_by.startswith("-")
        col_name = order_by.lstrip("-")
        if hasattr(Vehicle, col_name):
            col = getattr(Vehicle, col_name)
            query = query.order_by(col.desc() if desc else col.asc())

        query = query.offset(offset).limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().all()), total

    async def get_by_plate(self, plate: str) -> Vehicle | None:
        result = await self.session.execute(select(Vehicle).where(Vehicle.license_plate == plate))
        return result.scalar_one_or_none()

    async def get_by_vin(self, vin: str) -> Vehicle | None:
        result = await self.session.execute(select(Vehicle).where(Vehicle.vin == vin))
        return result.scalar_one_or_none()

    async def count_by_status(self) -> dict[str, int]:
        result = await self.session.execute(select(Vehicle.status, func.count()).group_by(Vehicle.status))
        return {row[0].value: row[1] for row in result.all()}
