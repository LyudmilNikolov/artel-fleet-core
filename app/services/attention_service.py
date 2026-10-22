"""Documents that need attention: one flat list shared by the bell, the inbox, the item page and the dashboard.

Detection reuses the existing "current entry" and status rules (``app/repositories/validity.py``): current entries
only, active drivers only, decommissioned vehicles excluded, documents without an end date never warn. "Today" is the
business-time-zone date (``local_today``). Document numbers are never part of an item.
"""

from dataclasses import dataclass, field, replace
from datetime import date
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.notification_rules import DRIVER, DRIVER_ENTITY, SOURCE_BY_CODE, VEHICLE, VEHICLE_ENTITY, source_code
from app.repositories.compliance_repo import ComplianceRepository
from app.repositories.driver_document_repo import DriverDocumentRepository
from app.services.driver_scope import UNRESTRICTED, DriverScope
from app.services.reminder_stop_service import ReminderStopService
from app.services.validity import local_today, to_local, validity_status

STATUSES = ("expired", "expiring_soon")
REMINDER_FILTERS = ("active", "stopped")


@dataclass(frozen=True)
class AttentionItem:
    """One current document that is expired or expiring soon."""

    key: str  # "<entity_type>:<entity_id>", stable per document entry (a renewal is a new entry)
    source: str  # "vehicle:casco", "driver:id_card", ...
    owner: str  # "vehicle" | "driver"
    entity_type: str  # "compliance_document" | "driver_document"
    entity_id: UUID
    subject: str  # licence plate or driver name
    subject_id: UUID
    end_date: date
    days_left: int  # negative once expired
    status: str  # "expired" | "expiring_soon"
    url: str  # item page
    document_url: str  # the document itself
    subject_url: str
    stopped: bool = False  # reminders were stopped for this entry (see ReminderStopService)
    first_seen_days: int | None = field(default=None, compare=False)  # days left when the entry was created
    search_text: str = field(default="", repr=False, compare=False)


def _item_url(entity_type: str, entity_id: UUID) -> str:
    return f"/notifications/item/{entity_type}/{entity_id}"


class AttentionService:
    def __init__(self, session: AsyncSession, today: date | None = None, scope: DriverScope = UNRESTRICTED):
        self.session = session
        self.today = today or local_today()
        self.scope = scope

    async def _all(self) -> list[AttentionItem]:
        items: list[AttentionItem] = []
        scope = self.scope
        vehicle_rows = await ComplianceRepository(self.session).attention_rows(
            today=self.today, window=settings.COMPLIANCE_EXPIRING_SOON_DAYS, vehicle_ids=scope.vehicle_filter
        )
        for entry, vehicle in vehicle_rows:
            items.append(
                self._item(
                    VEHICLE,
                    VEHICLE_ENTITY,
                    entry,
                    vehicle.license_plate,
                    vehicle.id,
                    f"/compliance/{entry.id}",
                    f"/vehicles/{vehicle.id}",
                    settings.COMPLIANCE_EXPIRING_SOON_DAYS,
                    f"{vehicle.license_plate} {vehicle.brand} {vehicle.model} {vehicle.vin or ''}",
                )
            )
        driver_rows = await DriverDocumentRepository(self.session).attention_rows(
            today=self.today,
            window=settings.DRIVER_DOCUMENT_EXPIRING_SOON_DAYS,
            driver_id=scope.driver_id,
            only_driver=scope.restricted,
        )
        for entry, driver in driver_rows:
            items.append(
                self._item(
                    DRIVER,
                    DRIVER_ENTITY,
                    entry,
                    driver.full_name,
                    driver.id,
                    f"/driver-documents/{entry.id}",
                    f"/drivers/{driver.id}",
                    settings.DRIVER_DOCUMENT_EXPIRING_SOON_DAYS,
                    driver.full_name,
                )
            )
        stops = await ReminderStopService(self.session).stopped_keys(
            [(item.entity_type, item.entity_id, item.end_date) for item in items]
        )
        items = [replace(item, stopped=True) if (item.entity_type, item.entity_id) in stops else item for item in items]
        # Expired first (negative days), then fewest days left.
        items.sort(key=lambda i: (i.days_left, i.subject.casefold(), i.source, str(i.entity_id)))
        return items

    def _item(self, owner, entity_type, entry, subject, subject_id, document_url, subject_url, window, search_text):
        return AttentionItem(
            key=f"{entity_type}:{entry.id}",
            source=source_code(entity_type, entry.document_type),
            owner=owner,
            entity_type=entity_type,
            entity_id=entry.id,
            subject=subject,
            subject_id=subject_id,
            end_date=entry.end_date,
            days_left=(entry.end_date - self.today).days,
            status=validity_status(entry.end_date, self.today, window),
            url=_item_url(entity_type, entry.id),
            document_url=document_url,
            subject_url=subject_url,
            search_text=search_text,
            first_seen_days=(entry.end_date - to_local(entry.created_at).date()).days,
        )

    async def items(
        self,
        source: str | None = None,
        status: str | None = None,
        q: str | None = None,
        reminders: str | None = None,
    ) -> list[AttentionItem]:
        """Attention items in the caller's scope, most urgent first, optionally filtered.

        ``reminders`` is "stopped" or "active" (anything else means no filter).
        """
        if source is not None and source not in SOURCE_BY_CODE:
            return []
        if status is not None and status not in STATUSES:
            return []
        needle = (q or "").strip().casefold()
        return [
            item
            for item in await self._all()
            if (source is None or item.source == source)
            and (status is None or item.status == status)
            and (not needle or needle in item.search_text.casefold())
            and (reminders not in REMINDER_FILTERS or item.stopped == (reminders == "stopped"))
        ]

    async def find(self, entity_type: str, entity_id: UUID) -> AttentionItem | None:
        """The item for one document entry, or None when it is not (or no longer) an attention item in scope."""
        key = f"{entity_type}:{entity_id}"
        return next((item for item in await self._all() if item.key == key), None)

    async def counts(self) -> dict[str, dict[str, int]]:
        """Expired / expiring-soon totals per owner (``vehicle`` and ``driver``)."""
        counts = {VEHICLE: {"expired": 0, "expiring_soon": 0}, DRIVER: {"expired": 0, "expiring_soon": 0}}
        for item in await self._all():
            counts[item.owner][item.status] += 1
        return counts


SORTS = ("urgency", "end_date", "subject")


def sort_items(items: list[AttentionItem], sort: str | None) -> list[AttentionItem]:
    """Reorder a list (it arrives most urgent first); unknown sorts keep the urgency order."""
    if sort == "end_date":
        return sorted(items, key=lambda i: (i.end_date, i.subject.casefold(), i.source, str(i.entity_id)))
    if sort == "subject":
        return sorted(items, key=lambda i: (i.subject.casefold(), i.days_left, i.source, str(i.entity_id)))
    return items


def neighbours(items: list[AttentionItem], key: str) -> tuple[AttentionItem | None, AttentionItem | None]:
    """The items before and after ``key`` in the list (None at the ends or when the key is not in the list)."""
    for index, item in enumerate(items):
        if item.key == key:
            return (items[index - 1] if index > 0 else None, items[index + 1] if index + 1 < len(items) else None)
    return None, None
