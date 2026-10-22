import secrets
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.compliance import ComplianceDocument
from app.models.document import Document, EntityType
from app.models.expense import Expense
from app.models.maintenance import MaintenanceRecord
from app.models.vehicle import Vehicle, VehicleStatus
from app.repositories.vehicle_repo import VehicleRepository, vehicle_search_condition
from app.schemas.mileage import MileageCreate
from app.schemas.vehicle import VehicleCreate, VehicleUpdate
from app.services.driver_service import check_driver_has_no_other_vehicle
from app.services.errors import DomainError
from app.services.mileage_service import MileageService
from app.utils.s3 import get_s3_client
from app.vehicle_rules import (
    APPLICABLE,
    FUEL_CHOICES,
    MILEAGE_TYPES,
    TECHNICAL_FIELDS,
    VehicleType,
    can_assign_to_driver,
    validate_applicability,
)


class VehicleService:
    def __init__(self, session: AsyncSession):
        self.repo = VehicleRepository(session)
        self.session = session

    async def departments(self) -> list[str]:
        from app.config import settings

        result = await self.session.scalars(select(Vehicle.department).distinct())
        return sorted({v.strip() for v in [*settings.VEHICLE_DEPARTMENTS, *result.all()] if v and v.strip()})

    async def _department(self, value):
        if value not in await self.departments():
            raise DomainError("error.vehicle_department")

    async def _unique(self, values, vehicle=None):
        for field, method, key in (
            ("license_plate", self.repo.get_by_plate, "error.plate_in_use"),
            ("vin", self.repo.get_by_vin, "error.vin_in_use"),
        ):
            if values.get(field):
                other = await method(values[field])
                if other and (vehicle is None or other.id != vehicle.id):
                    raise DomainError(key)

    @staticmethod
    def _integrity_error(exc):
        message = str(exc.orig)
        if "vin" in message:
            return DomainError("error.vin_in_use")
        if "license_plate" in message:
            return DomainError("error.plate_in_use")
        return DomainError("errors.invalid_input")

    async def create(self, data: VehicleCreate, user_id: UUID | None = None) -> Vehicle:
        values = data.model_dump()
        await self._department(values["department"])
        if values.get("assigned_driver_id"):
            if not can_assign_to_driver(values["vehicle_type"]):
                raise DomainError("error.vehicle_not_assignable")
            await check_driver_has_no_other_vehicle(self.session, values["assigned_driver_id"], None)
        mileage = values.pop("current_mileage")
        forklift = values["vehicle_type"] == VehicleType.FORKLIFT
        for attempt in range(5):
            if forklift:
                values["license_plate"] = "PRIVATE-" + secrets.token_hex(4).upper()
            await self._unique({k: v for k, v in values.items() if k != "license_plate" or not forklift})
            try:
                async with self.session.begin_nested():
                    vehicle = await self.repo.create(**values)
                    if mileage is not None:
                        await MileageService(self.session).add_reading(
                            MileageCreate(vehicle_id=vehicle.id, value=mileage), user_id
                        )
                # Mileage writes expire the server-generated updated_at attribute.
                # Load it explicitly before response serialization leaves async context.
                await self.session.refresh(vehicle)
                return vehicle
            except IntegrityError as exc:
                if forklift and "license_plate" in str(exc.orig):
                    continue
                raise self._integrity_error(exc) from exc
        raise DomainError("error.vehicle_identifier_retry")

    async def update(self, vehicle_id: UUID, data: VehicleUpdate, user_id: UUID | None = None) -> Vehicle:
        vehicle = await self.session.scalar(
            select(Vehicle).where(Vehicle.id == vehicle_id).with_for_update().execution_options(populate_existing=True)
        )
        if not vehicle:
            raise DomainError("error.vehicle_not_found")
        values = data.model_dump(exclude_unset=True)
        kind = values.get("vehicle_type", vehicle.vehicle_type)
        changed = kind != vehicle.vehicle_type
        try:
            validate_applicability(kind, values)
        except ValueError as exc:
            raise DomainError(str(exc)) from exc
        if "department" in values:
            await self._department(values["department"])
        if not can_assign_to_driver(kind):
            if values.get("assigned_driver_id"):
                raise DomainError("error.vehicle_not_assignable")
            if changed:  # becoming a trailer / forklift ends any driver assignment
                values["assigned_driver_id"] = None
        new_driver = values.get("assigned_driver_id")
        if new_driver and new_driver != vehicle.assigned_driver_id:
            if vehicle.assigned_driver_id is not None:
                raise DomainError("error.vehicle_already_assigned")  # unassign first, never take it over
            await check_driver_has_no_other_vehicle(self.session, new_driver, vehicle.id)
        supplied_plate = values.get("license_plate")
        generate = changed and kind == VehicleType.FORKLIFT
        if (
            vehicle.vehicle_type == VehicleType.FORKLIFT
            and not changed
            and supplied_plate not in (None, vehicle.license_plate)
        ):
            raise DomainError("error.forklift_identifier_readonly")
        if generate and supplied_plate not in (None, vehicle.license_plate):
            raise DomainError("error.forklift_identifier_readonly")
        if kind != VehicleType.FORKLIFT:
            plate = supplied_plate or (None if vehicle.vehicle_type == VehicleType.FORKLIFT else vehicle.license_plate)
            if not plate or plate.startswith("PRIVATE-"):
                raise DomainError("error.road_plate_required")
        mileage_supplied = "current_mileage" in values
        reading = values.pop("current_mileage", None)
        historical = await MileageService.logged_baseline(self.session, vehicle.id)
        if kind in MILEAGE_TYPES and mileage_supplied and reading is None and historical is not None:
            raise DomainError("error.mileage_cannot_clear")
        if changed:
            values.update({field: None for field in TECHNICAL_FIELDS - APPLICABLE[kind]})
            if "fuel_type" not in data.model_fields_set and vehicle.fuel_type not in FUEL_CHOICES.get(kind, ()):
                values["fuel_type"] = None
        if kind in MILEAGE_TYPES:
            baseline = max(v for v in (vehicle.current_mileage, historical, 0) if v is not None)
            if reading is not None and reading < baseline:
                raise DomainError("error.mileage_decreased", value=reading, previous=baseline)
            if changed and historical is not None:
                values["current_mileage"] = historical
            elif mileage_supplied and reading is None:
                values["current_mileage"] = None
        await self._unique({k: v for k, v in values.items() if k != "license_plate" or not generate}, vehicle)
        for attempt in range(5):
            if generate:
                values["license_plate"] = "PRIVATE-" + secrets.token_hex(4).upper()
            try:
                async with self.session.begin_nested():
                    await self.repo.update(vehicle, **values)
                    if reading is not None and reading != vehicle.current_mileage:
                        await MileageService(self.session).add_reading(
                            MileageCreate(vehicle_id=vehicle.id, value=reading), user_id
                        )
                # Mileage writes expire the server-generated updated_at attribute.
                # Load it explicitly before response serialization leaves async context.
                await self.session.refresh(vehicle)
                return vehicle
            except IntegrityError as exc:
                await self.session.refresh(vehicle)
                if generate and "license_plate" in str(exc.orig):
                    continue
                raise self._integrity_error(exc) from exc
        raise DomainError("error.vehicle_identifier_retry")

    async def picker_options(
        self, q: str | None, limit: int = 20, vehicle_types=None, vehicle_ids=None, unassigned_only: bool = False
    ) -> list[Vehicle]:
        """Vehicles for the searchable picker, matched by plate, VIN, brand or model; optionally one type group."""
        query = select(Vehicle)
        search = vehicle_search_condition(q)
        if search is not None:
            query = query.where(search)
        if vehicle_types:
            query = query.where(Vehicle.vehicle_type.in_(list(vehicle_types)))
        if vehicle_ids is not None:  # a driver only picks their assigned vehicles
            query = query.where(Vehicle.id.in_(list(vehicle_ids)))
        if unassigned_only:  # vehicles that already have a driver are not offered for assignment
            query = query.where(Vehicle.assigned_driver_id.is_(None))
        return list((await self.session.scalars(query.order_by(Vehicle.license_plate).limit(limit))).all())

    async def get_by_id(self, vehicle_id: UUID) -> Vehicle | None:
        return await self.repo.get_by_id(vehicle_id)

    async def list_vehicles(
        self,
        *,
        q: str | None = None,
        status: VehicleStatus | None = None,
        brand: str | None = None,
        fuel_type: str | None = None,
        vehicle_type: str | Sequence[str] | None = None,
        department: str | None = None,
        vehicle_ids: Sequence | None = None,
        page: int = 1,
        size: int = 50,
        order_by: str = "-created_at",
    ) -> tuple[list[Vehicle], int]:
        """Vehicles matching the filters; ``vehicle_ids`` restricts to those vehicles (a driver's assigned ones)."""
        return await self.repo.search(
            ids=vehicle_ids,
            q=q,
            status=status,
            brand=brand,
            fuel_type=fuel_type,
            vehicle_type=vehicle_type,
            department=department,
            offset=(page - 1) * size,
            limit=size,
            order_by=order_by,
        )

    async def delete(self, vehicle_id: UUID) -> None:
        vehicle = await self.repo.get_by_id(vehicle_id)
        if not vehicle:
            raise DomainError("error.vehicle_not_found")
        # Related maintenance, expense, compliance document and mileage rows go with the vehicle (ON DELETE CASCADE);
        # their documents are polymorphic, so they are removed here along with the stored files.
        related = [(EntityType.VEHICLE, [vehicle.id])]
        for kind, model in (
            (EntityType.MAINTENANCE, MaintenanceRecord),
            (EntityType.EXPENSE, Expense),
            (EntityType.COMPLIANCE_DOCUMENT, ComplianceDocument),
        ):
            child_ids = await self.session.scalars(select(model.id).where(model.vehicle_id == vehicle.id))
            related.append((kind, list(child_ids.all())))
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
        await self.repo.delete(vehicle)
        await self.session.flush()
        s3 = get_s3_client()
        for key in keys:
            try:
                s3.delete_file(key)
            except Exception:  # noqa: BLE001 - the database rows are already gone; do not fail the delete
                pass

    async def count_by_status(self) -> dict[str, int]:
        return await self.repo.count_by_status()
