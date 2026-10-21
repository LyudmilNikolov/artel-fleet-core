from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.driver import Driver, DriverStatus
from app.repositories.driver_repo import DriverRepository
from app.schemas.driver import DriverCreate, DriverUpdate
from app.services.errors import DomainError


class DriverService:
    def __init__(self, session: AsyncSession):
        self.repo = DriverRepository(session)

    async def create(self, data: DriverCreate) -> Driver:
        return await self.repo.create(**data.model_dump())

    async def update(self, driver_id: UUID, data: DriverUpdate) -> Driver:
        driver = await self.repo.get_by_id(driver_id)
        if not driver:
            raise DomainError("error.driver_not_found")
        return await self.repo.update(driver, **data.model_dump(exclude_unset=True))

    async def get_by_id(self, driver_id: UUID) -> Driver | None:
        return await self.repo.get_by_id(driver_id)

    async def list_drivers(
        self,
        *,
        q: str | None = None,
        status: DriverStatus | None = None,
        department: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[Driver], int]:
        return await self.repo.search(q=q, status=status, department=department, offset=(page - 1) * size, limit=size)

    async def delete(self, driver_id: UUID) -> None:
        driver = await self.repo.get_by_id(driver_id)
        if not driver:
            raise DomainError("error.driver_not_found")
        await self.repo.delete(driver)
