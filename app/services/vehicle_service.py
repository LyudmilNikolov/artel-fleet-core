from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vehicle import Vehicle, VehicleStatus
from app.repositories.vehicle_repo import VehicleRepository
from app.schemas.vehicle import VehicleCreate, VehicleUpdate
from app.services.errors import DomainError


class VehicleService:
    def __init__(self, session: AsyncSession):
        self.repo = VehicleRepository(session)
        self.session = session

    async def create(self, data: VehicleCreate) -> Vehicle:
        """Create a new vehicle with uniqueness checks."""
        existing = await self.repo.get_by_plate(data.license_plate)
        if existing:
            raise DomainError("error.plate_exists", plate=data.license_plate)

        existing = await self.repo.get_by_vin(data.vin)
        if existing:
            raise DomainError("error.vin_exists", vin=data.vin)

        return await self.repo.create(**data.model_dump())

    async def update(self, vehicle_id: UUID, data: VehicleUpdate) -> Vehicle:
        """Update vehicle fields."""
        vehicle = await self.repo.get_by_id(vehicle_id)
        if not vehicle:
            raise DomainError("error.vehicle_not_found")

        update_data = data.model_dump(exclude_unset=True)

        if "license_plate" in update_data and update_data["license_plate"] != vehicle.license_plate:
            existing = await self.repo.get_by_plate(update_data["license_plate"])
            if existing:
                raise DomainError("error.plate_in_use")

        if "vin" in update_data and update_data["vin"] != vehicle.vin:
            existing = await self.repo.get_by_vin(update_data["vin"])
            if existing:
                raise DomainError("error.vin_in_use")

        return await self.repo.update(vehicle, **update_data)

    async def get_by_id(self, vehicle_id: UUID) -> Vehicle | None:
        return await self.repo.get_by_id(vehicle_id)

    async def list_vehicles(
        self,
        *,
        q: str | None = None,
        status: VehicleStatus | None = None,
        brand: str | None = None,
        fuel_type: str | None = None,
        body_type: str | None = None,
        department: str | None = None,
        page: int = 1,
        size: int = 50,
        order_by: str = "-created_at",
    ) -> tuple[list[Vehicle], int]:
        return await self.repo.search(
            q=q,
            status=status,
            brand=brand,
            fuel_type=fuel_type,
            body_type=body_type,
            department=department,
            offset=(page - 1) * size,
            limit=size,
            order_by=order_by,
        )

    async def delete(self, vehicle_id: UUID) -> None:
        vehicle = await self.repo.get_by_id(vehicle_id)
        if not vehicle:
            raise DomainError("error.vehicle_not_found")
        await self.repo.delete(vehicle)

    async def count_by_status(self) -> dict[str, int]:
        return await self.repo.count_by_status()
