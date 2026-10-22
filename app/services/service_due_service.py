"""Next due km / date of planned service items (engine oil, gearbox oil, ...), computed and never stored.

Per (vehicle, service item) the latest completed planned service is current; its next service km and
date are compared with the vehicle's current mileage and today's date. Older records never warn.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.maintenance_rules import DUE_STATUSES, SERVICE_ITEMS, ServiceDue, service_due
from app.models.maintenance import MaintenanceRecord
from app.models.vehicle import Vehicle
from app.repositories.maintenance_due import current_service_query
from app.services.errors import DomainError
from app.services.validity import local_today

_RANK = {status: i for i, status in enumerate(DUE_STATUSES)}


@dataclass
class ServiceDueView:
    """A current planned service with its computed due state."""

    record: MaintenanceRecord
    vehicle: Vehicle
    due: ServiceDue

    @property
    def status(self) -> str:
        return self.due.status


class ServiceDueService:
    def __init__(
        self, session: AsyncSession, today: date | None = None, km_window: int | None = None, day_window: int | None = None
    ):
        self.session = session
        self.today = today or local_today()
        self.km_window = settings.SERVICE_DUE_SOON_KM if km_window is None else km_window
        self.day_window = settings.SERVICE_DUE_SOON_DAYS if day_window is None else day_window

    def _state(self, record: MaintenanceRecord, vehicle: Vehicle) -> ServiceDue:
        return service_due(
            record.next_service_mileage,
            record.next_service_date,
            vehicle.current_mileage,
            self.today,
            self.km_window,
            self.day_window,
        )

    async def _views(self, vehicle_id: UUID | None = None) -> list[ServiceDueView]:
        rows = (await self.session.execute(current_service_query(vehicle_id))).all()
        return [ServiceDueView(record, vehicle, self._state(record, vehicle)) for record, vehicle in rows]

    @staticmethod
    def _urgency(view: ServiceDueView):
        due = view.due
        return (
            _RANK[due.status],
            due.days_left if due.days_left is not None else 10**9,
            due.km_left if due.km_left is not None else 10**9,
            view.vehicle.license_plate or "",
            SERVICE_ITEMS.index(view.record.service_item) if view.record.service_item in SERVICE_ITEMS else 99,
        )

    async def list_due(
        self, status: str | None = None, vehicle_id: UUID | None = None, vehicle_ids: Sequence[UUID] | None = None
    ) -> list[ServiceDueView]:
        """Current planned services, most urgent first; optionally only one status."""
        if status is not None and status not in DUE_STATUSES:
            raise DomainError("errors.invalid_input")
        views = [
            v
            for v in await self._views(vehicle_id)
            if (status is None or v.status == status) and (vehicle_ids is None or v.vehicle.id in vehicle_ids)
        ]
        return sorted(views, key=self._urgency)

    async def for_vehicle(self, vehicle_id: UUID) -> list[ServiceDueView]:
        """Every tracked service item of one vehicle in display order (including decommissioned vehicles)."""
        rows = (await self.session.execute(current_service_query(vehicle_id, include_decommissioned=True))).all()
        views = [ServiceDueView(record, vehicle, self._state(record, vehicle)) for record, vehicle in rows]
        return sorted(
            views, key=lambda v: SERVICE_ITEMS.index(v.record.service_item) if v.record.service_item in SERVICE_ITEMS else 99
        )

    async def attention_counts(self) -> dict[str, int]:
        """Current planned services that are overdue or due soon (decommissioned vehicles excluded)."""
        views = await self._views()
        return {s: sum(1 for v in views if v.status == s) for s in ("overdue", "due_soon")}

    async def state_for_record(self, record: MaintenanceRecord) -> ServiceDue | None:
        """The due state when ``record`` is the current planned service of its item, else None."""
        if record.service_item is None:
            return None
        for view in await self.for_vehicle(record.vehicle_id):
            if view.record.id == record.id:
                return view.due
        return None
