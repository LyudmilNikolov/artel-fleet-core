from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.maintenance_rules import SERVICE_CHECKLIST, normalize_checklist, validate_service_selection
from app.models.document import Document, EntityType
from app.models.maintenance import MaintenanceRecord, MaintenanceStatus, MaintenanceType
from app.config import settings
from app.models.vehicle import Vehicle
from app.repositories.base import BaseRepository
from app.repositories.vehicle_repo import vehicle_search_condition
from app.schemas.maintenance import MaintenanceCreate, MaintenanceUpdate
from app.services.errors import DomainError
from app.services.mileage_service import MileageService
from app.utils.s3 import get_s3_client
from app.vehicle_rules import VehicleType


PENDING_STATUSES = (MaintenanceStatus.SCHEDULED, MaintenanceStatus.IN_PROGRESS)


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


def _check_planned_service(record_type, service_item, mileage_at_service, next_service_mileage) -> None:
    """A service item belongs to planned services only; the next due km must lie after the service km."""
    if service_item is not None and record_type != MaintenanceType.SCHEDULED_SERVICE:
        raise DomainError("error.maintenance_service_item_not_allowed")
    if next_service_mileage is not None and mileage_at_service is not None and next_service_mileage <= mileage_at_service:
        raise DomainError("error.maintenance_next_km_not_after")


def _check_selection(vehicle_type, sent_item, effective_item, checklist) -> None:
    """The item (when sent) must fit the vehicle type; the checklist must fit the effective item."""
    try:
        validate_service_selection(vehicle_type, sent_item, None)
        validate_service_selection(None, effective_item, checklist)
    except ValueError as exc:
        raise DomainError(str(exc)) from exc


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
        values["service_checklist"] = normalize_checklist(values["service_checklist"])
        _check_planned_service(
            values["type"], values["service_item"], values["mileage_at_service"], values["next_service_mileage"]
        )
        if values["type"] != MaintenanceType.SCHEDULED_SERVICE and values["service_checklist"]:
            raise DomainError("error.maintenance_service_item_not_allowed")
        _check_selection(vehicle.vehicle_type, values["service_item"], values["service_item"], values["service_checklist"])
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
        new_type = values.get("type", record.type)
        if "service_checklist" in values:
            values["service_checklist"] = normalize_checklist(values["service_checklist"])
        if new_type != MaintenanceType.SCHEDULED_SERVICE:
            # Changing a planned service into another type drops its item and checklist; sending them is an error.
            _check_planned_service(new_type, values.get("service_item"), None, None)
            if values.get("service_checklist"):
                raise DomainError("error.maintenance_service_item_not_allowed")
            values["service_item"] = None
            values["service_checklist"] = None
        else:
            item = values["service_item"] if "service_item" in values else record.service_item
            if "service_item" in values and "service_checklist" not in values:
                # A new item keeps only the checks it offers (none for items without a checklist).
                allowed = SERVICE_CHECKLIST.get(item or "", ())
                kept = [c for c in (record.service_checklist or []) if c in allowed]
                values["service_checklist"] = kept or None
            checklist = values["service_checklist"] if "service_checklist" in values else record.service_checklist
            _check_selection(record.vehicle.vehicle_type, values.get("service_item"), item, checklist)
        _check_planned_service(
            new_type,
            values.get("service_item", record.service_item),
            values.get("mileage_at_service", record.mileage_at_service),
            values.get("next_service_mileage", record.next_service_mileage),
        )
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
        self,
        *,
        status: MaintenanceStatus | Sequence[MaintenanceStatus] | None = None,
        q: str | None = None,
        vehicle_types: Sequence[VehicleType] | None = None,
        when: str | None = None,
        today: date | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[MaintenanceRecord], int]:
        """List records; filter by status, vehicle (search text, vehicle type) and when they are due.

        ``when`` = ``overdue`` (scheduled, date passed) or ``upcoming`` (scheduled within
        ``MAINTENANCE_UPCOMING_DAYS``). Pending lists (scheduled / in progress) and these filters sort the
        soonest first; everything else newest first.
        """
        if when not in (None, "overdue", "upcoming"):
            raise DomainError("errors.invalid_input")
        today = today or _today()
        rec = MaintenanceRecord
        conditions = []
        statuses = [status] if isinstance(status, MaintenanceStatus) else list(status or [])
        if statuses:
            conditions.append(rec.status.in_(statuses))
        if when:
            conditions += [rec.status == MaintenanceStatus.SCHEDULED, rec.scheduled_date.is_not(None)]
            if when == "overdue":
                conditions.append(rec.scheduled_date < today)
            else:
                soon = today + timedelta(days=settings.MAINTENANCE_UPCOMING_DAYS)
                conditions += [rec.scheduled_date >= today, rec.scheduled_date <= soon]
        search = vehicle_search_condition(q)
        if search is not None:
            conditions.append(search)
        if vehicle_types:
            conditions.append(Vehicle.vehicle_type.in_(list(vehicle_types)))
        joined = select(rec).join(Vehicle, Vehicle.id == rec.vehicle_id).where(*conditions)
        total = (
            await self.session.execute(
                select(func.count()).select_from(rec).join(Vehicle, Vehicle.id == rec.vehicle_id).where(*conditions)
            )
        ).scalar() or 0
        pending = bool(when) or (bool(statuses) and all(s in PENDING_STATUSES for s in statuses))
        order = (rec.scheduled_date.asc().nulls_last(), rec.created_at) if pending else (
            rec.scheduled_date.desc().nulls_last(),
            rec.created_at.desc(),
        )
        result = await self.session.execute(joined.order_by(*order, rec.id).offset((page - 1) * size).limit(size))
        return list(result.scalars().all()), total

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
