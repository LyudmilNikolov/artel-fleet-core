"""Data access for per-driver tracked documents."""

from datetime import date
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.driver import Driver, DriverStatus
from app.models.driver_document import DriverDocument
from app.repositories.base import BaseRepository
from app.repositories.validity import current_ids as _current_ids
from app.repositories.validity import status_clause as _status_clause

STATUSES = ("valid", "expiring_soon", "expired", "no_expiry")
ALERT_STATUSES = ("expired", "expiring_soon")


def current_ids() -> Select:
    """IDs of the current entry per (driver, type).

    Latest end date wins (no end date counts as latest). An entry that has been renewed is never
    current, so renewing a no-expiry document hands over to its successor.
    """
    successor = aliased(DriverDocument)
    not_renewed = ~select(successor.id).where(successor.renewed_from_id == DriverDocument.id).exists()
    return _current_ids(DriverDocument, DriverDocument.driver_id, not_renewed)


def status_clause(status: str, today: date, window: int):
    """SQL condition for a computed status on DriverDocument.end_date."""
    return _status_clause(DriverDocument.end_date, status, today, window)


def drivers_with_status(status: str, today: date, window: int) -> Select:
    """Driver IDs that have a current entry in the given status."""
    return select(DriverDocument.driver_id).where(
        DriverDocument.id.in_(current_ids().scalar_subquery()), status_clause(status, today, window)
    )


class DriverDocumentRepository(BaseRepository[DriverDocument]):
    def __init__(self, session: AsyncSession):
        super().__init__(DriverDocument, session)

    def _rows(self) -> Select:
        doc = DriverDocument
        return select(doc, Driver, doc.id.in_(current_ids().scalar_subquery()).label("is_current")).join(
            Driver, Driver.id == doc.driver_id
        )

    async def get_row(self, entry_id: UUID):
        """Return (entry, driver, is_current) or None."""
        result = await self.session.execute(self._rows().where(DriverDocument.id == entry_id))
        return result.first()

    async def search(
        self,
        *,
        today: date,
        window: int,
        driver_id: UUID | None = None,
        document_type: str | None = None,
        status: str | None = None,
        current_only: bool = True,
        offset: int = 0,
        limit: int = 50,
    ):
        """Return ([(entry, driver, is_current)], total), no-expiry entries last, soonest end date first."""
        doc = DriverDocument
        conditions = []
        if driver_id:
            conditions.append(doc.driver_id == driver_id)
        if document_type:
            conditions.append(doc.document_type == document_type)
        if status:
            conditions.append(status_clause(status, today, window))
        if current_only:
            conditions.append(doc.id.in_(current_ids().scalar_subquery()))
        total = (await self.session.execute(select(func.count()).select_from(doc).where(*conditions))).scalar() or 0
        result = await self.session.execute(
            self._rows()
            .where(*conditions)
            .order_by(doc.end_date.asc().nulls_last(), Driver.full_name.asc(), doc.document_type.asc(), doc.id)
            .offset(offset)
            .limit(limit)
        )
        return list(result.all()), total

    async def for_driver(self, driver_id: UUID):
        """All entries of one driver, grouped by type, newest first, with the current flag."""
        doc = DriverDocument
        result = await self.session.execute(
            self._rows()
            .where(doc.driver_id == driver_id)
            .order_by(
                doc.document_type,
                doc.end_date.desc().nulls_first(),
                doc.start_date.desc().nulls_last(),
                doc.created_at.desc(),
            )
        )
        return list(result.all())

    async def count_current(self, *, today: date, window: int, status: str) -> int:
        """Count current entries with a status for active drivers."""
        doc = DriverDocument
        query = (
            select(func.count())
            .select_from(doc)
            .join(Driver, Driver.id == doc.driver_id)
            .where(
                doc.id.in_(current_ids().scalar_subquery()),
                Driver.status == DriverStatus.ACTIVE,
                status_clause(status, today, window),
            )
        )
        return (await self.session.execute(query)).scalar() or 0

    async def alert_counts(self, driver_ids: list[UUID], *, today: date, window: int) -> dict[UUID, dict[str, int]]:
        """Per driver, the number of current entries that are expired or expiring soon."""
        counts: dict[UUID, dict[str, int]] = {}
        if not driver_ids:
            return counts
        doc = DriverDocument
        for status in ALERT_STATUSES:
            rows = await self.session.execute(
                select(doc.driver_id, func.count())
                .where(
                    doc.driver_id.in_(driver_ids),
                    doc.id.in_(current_ids().scalar_subquery()),
                    status_clause(status, today, window),
                )
                .group_by(doc.driver_id)
            )
            for driver_id, count in rows.all():
                counts.setdefault(driver_id, {"expired": 0, "expiring_soon": 0})[status] = count
        return counts

    async def same_document(self, driver_id: UUID, document_type: str) -> list[DriverDocument]:
        """Every entry (current and history) of one driver and type."""
        result = await self.session.scalars(
            select(DriverDocument).where(
                DriverDocument.driver_id == driver_id, DriverDocument.document_type == document_type
            )
        )
        return list(result.all())

    async def count_same_document(self, driver_id: UUID, document_type: str) -> int:
        """Number of entries of one driver and type."""
        return (
            await self.session.scalar(
                select(func.count())
                .select_from(DriverDocument)
                .where(DriverDocument.driver_id == driver_id, DriverDocument.document_type == document_type)
            )
            or 0
        )

    async def successor_of(self, entry_id: UUID) -> DriverDocument | None:
        """The entry that was created by renewing ``entry_id``, if any."""
        return await self.session.scalar(
            select(DriverDocument).where(DriverDocument.renewed_from_id == entry_id).limit(1)
        )

    async def lock_driver(self, driver_id: UUID) -> Driver | None:
        """Load a driver with FOR SHARE so a concurrent delete cannot interleave."""
        return await self.session.scalar(
            select(Driver)
            .where(Driver.id == driver_id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
