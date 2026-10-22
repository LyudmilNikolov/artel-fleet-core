from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.document import Document, EntityType
from app.models.driver import Driver, DriverStatus
from app.models.driver_document import DriverDocument
from app.models.user import User, UserRole
from app.models.vehicle import Vehicle
from app.repositories.driver_repo import DriverRepository
from app.schemas.driver import DriverCreate, DriverUpdate
from app.services.errors import DomainError
from app.services.validity import utc_today
from app.utils.s3 import get_s3_client


class DriverService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = DriverRepository(session)

    async def create(self, data: DriverCreate) -> Driver:
        """Create a driver. Licence and other documents are added afterwards as driver documents.

        A ``user_id`` (API) must belong to an existing driver-role login that has no profile yet.
        """
        if data.user_id is not None:
            user = await self.session.get(User, data.user_id)
            if user is None or user.role != UserRole.DRIVER:
                raise DomainError("error.driver_login_invalid")
            if await self.repo.get_for_user(user.id) is not None:
                raise DomainError("error.driver_login_taken")
        return await self.repo.create(**data.model_dump())

    async def update(self, driver_id: UUID, data: DriverUpdate) -> Driver:
        """Update the fields that were sent; raises ``error.driver_not_found`` for an unknown driver."""
        driver = await self.repo.get_by_id(driver_id)
        if not driver:
            raise DomainError("error.driver_not_found")
        return await self.repo.update(driver, **data.model_dump(exclude_unset=True))

    async def get_by_id(self, driver_id: UUID) -> Driver | None:
        """Return one driver, or None."""
        return await self.repo.get_by_id(driver_id)

    async def get_for_user(self, user_id: UUID) -> Driver | None:
        """The driver record linked to a system user, or None."""
        return await self.repo.get_for_user(user_id)

    async def list_drivers(
        self,
        *,
        q: str | None = None,
        status: DriverStatus | None = None,
        exclude_status: DriverStatus | None = None,
        department: str | None = None,
        doc_status: str | None = None,
        user_id: UUID | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[Driver], int]:
        """List drivers, optionally only those with a current expired/expiring-soon document."""
        if doc_status is not None and doc_status not in ("expired", "expiring_soon"):
            raise DomainError("errors.invalid_input")
        return await self.repo.search(
            q=q,
            status=status,
            exclude_status=exclude_status,
            department=department,
            doc_status=doc_status,
            today=utc_today(),
            window=settings.DRIVER_DOCUMENT_EXPIRING_SOON_DAYS,
            user_id=user_id,
            offset=(page - 1) * size,
            limit=size,
        )

    async def assigned_vehicles(self, driver_id: UUID) -> list[Vehicle]:
        """Vehicles currently assigned to the driver, ordered by plate."""
        result = await self.session.scalars(
            select(Vehicle).where(Vehicle.assigned_driver_id == driver_id).order_by(Vehicle.license_plate)
        )
        return list(result.all())

    async def assign_vehicle(self, driver_id: UUID, vehicle_id: UUID) -> Vehicle:
        """Assign a vehicle to a driver (moving it from another driver if needed).

        A driver may have several vehicles; a terminated driver cannot be assigned any.
        """
        driver = await self.repo.get_by_id(driver_id)
        if not driver:
            raise DomainError("error.driver_not_found")
        if driver.status == DriverStatus.TERMINATED:
            raise DomainError("error.driver_terminated_assign")
        vehicle = await self.session.get(Vehicle, vehicle_id)
        if not vehicle:
            raise DomainError("error.vehicle_not_found")
        vehicle.assigned_driver_id = driver.id
        await self.session.flush()
        return vehicle

    async def unassign_vehicle(self, driver_id: UUID, vehicle_id: UUID) -> None:
        """Remove the driver's assignment from a vehicle; the vehicle must currently belong to this driver."""
        vehicle = await self.session.get(Vehicle, vehicle_id)
        if not vehicle or vehicle.assigned_driver_id != driver_id:
            raise DomainError("error.vehicle_not_assigned")
        vehicle.assigned_driver_id = None
        await self.session.flush()

    async def delete(self, driver_id: UUID) -> None:
        """Delete a driver with its document entries, uploaded files and stored objects.

        Document entries go with the driver (ON DELETE CASCADE); attachments are polymorphic, so
        they are removed here, and the stored files afterwards on a best-effort basis.
        """
        driver = await self.repo.get_by_id(driver_id)
        if not driver:
            raise DomainError("error.driver_not_found")
        entry_ids = list((await self.session.scalars(select(DriverDocument.id).where(DriverDocument.driver_id == driver.id))).all())
        related = [(EntityType.DRIVER, [driver.id]), (EntityType.DRIVER_DOCUMENT, entry_ids)]
        documents = []
        for kind, ids in related:
            if ids:
                found = await self.session.scalars(
                    select(Document).where(Document.entity_type == kind, Document.entity_id.in_(ids))
                )
                documents.extend(found.all())
        keys = [doc.s3_key for doc in documents]
        for doc in documents:
            await self.session.delete(doc)
        await self.repo.delete(driver)
        await self.session.flush()
        if keys:
            s3 = get_s3_client()
            for key in keys:
                try:
                    s3.delete_file(key)
                except Exception:  # noqa: BLE001 - the database rows are already gone; do not fail the delete
                    pass
