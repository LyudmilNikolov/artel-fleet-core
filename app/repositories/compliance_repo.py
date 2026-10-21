"""Data access for per-vehicle compliance documents."""

from datetime import date
from uuid import UUID

from sqlalchemy import Select, and_, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.compliance import ComplianceDocument
from app.models.vehicle import Vehicle, VehicleStatus, VehicleType
from app.repositories.base import BaseRepository
from app.repositories.validity import STATUSES  # noqa: F401  (re-exported)
from app.repositories.validity import current_ids as _current_ids
from app.repositories.validity import status_clause as _status_clause
from app.vehicle_rules import COMPLIANCE_TYPES, COMPLIANCE_VEHICLE_TYPES


def current_ids() -> Select:
    """IDs of the current entry per (vehicle, type): latest end_date, then start_date, created_at, id."""
    return _current_ids(ComplianceDocument, ComplianceDocument.vehicle_id)


def applicable_clause():
    """True when the entry's type applies to its vehicle's current type (requires a join to Vehicle)."""
    pairs = [
        and_(Vehicle.vehicle_type == VehicleType(vehicle_type), ComplianceDocument.document_type.in_(types))
        for vehicle_type, types in COMPLIANCE_TYPES.items()
        if types
    ]
    return or_(*pairs) if pairs else false()


def status_clause(status: str, today: date, window: int):
    """SQL condition for a computed status on ComplianceDocument.end_date."""
    return _status_clause(ComplianceDocument.end_date, status, today, window)


class ComplianceRepository(BaseRepository[ComplianceDocument]):
    def __init__(self, session: AsyncSession):
        super().__init__(ComplianceDocument, session)

    def _rows(self) -> Select:
        doc = ComplianceDocument
        return select(
            doc,
            Vehicle,
            doc.id.in_(current_ids().scalar_subquery()).label("is_current"),
            applicable_clause().label("applicable"),
        ).join(Vehicle, Vehicle.id == doc.vehicle_id)

    async def get_row(self, entry_id: UUID):
        """Return (entry, vehicle, is_current, applicable) or None."""
        result = await self.session.execute(self._rows().where(ComplianceDocument.id == entry_id))
        return result.first()

    async def search(
        self,
        *,
        today: date,
        window: int,
        vehicle_id: UUID | None = None,
        q: str | None = None,
        document_type: str | None = None,
        status: str | None = None,
        current_only: bool = True,
        offset: int = 0,
        limit: int = 50,
    ):
        """Return ([(entry, vehicle, is_current, applicable)], total) ordered most urgent first."""
        doc = ComplianceDocument
        conditions = []
        if vehicle_id:
            conditions.append(doc.vehicle_id == vehicle_id)
        if q:
            pattern = f"%{q.strip()}%"
            conditions.append(
                or_(
                    Vehicle.license_plate.ilike(pattern),
                    Vehicle.brand.ilike(pattern),
                    Vehicle.model.ilike(pattern),
                    Vehicle.vin.ilike(pattern),
                )
            )
        if document_type:
            conditions.append(doc.document_type == document_type)
        if status:
            conditions.append(status_clause(status, today, window))
        if current_only:
            conditions.append(doc.id.in_(current_ids().scalar_subquery()))
        query = self._rows().where(*conditions)
        count_query = (
            select(func.count()).select_from(doc).join(Vehicle, Vehicle.id == doc.vehicle_id).where(*conditions)
        )
        total = (await self.session.execute(count_query)).scalar() or 0
        result = await self.session.execute(
            query.order_by(doc.end_date.asc(), Vehicle.license_plate.asc(), doc.document_type.asc(), doc.id)
            .offset(offset)
            .limit(limit)
        )
        return list(result.all()), total

    async def for_vehicle(self, vehicle_id: UUID):
        """All entries of one vehicle, newest end_date first, with current/applicable flags."""
        doc = ComplianceDocument
        result = await self.session.execute(
            self._rows()
            .where(doc.vehicle_id == vehicle_id)
            .order_by(doc.document_type, doc.end_date.desc(), doc.start_date.desc(), doc.created_at.desc())
        )
        return list(result.all())

    async def count_current(self, *, today: date, window: int, status: str) -> int:
        """Count current, applicable entries with a status, excluding decommissioned vehicles."""
        doc = ComplianceDocument
        query = (
            select(func.count())
            .select_from(doc)
            .join(Vehicle, Vehicle.id == doc.vehicle_id)
            .where(
                doc.id.in_(current_ids().scalar_subquery()),
                applicable_clause(),
                Vehicle.status != VehicleStatus.DECOMMISSIONED,
                status_clause(status, today, window),
            )
        )
        return (await self.session.execute(query)).scalar() or 0

    async def same_document(self, vehicle_id: UUID, document_type: str) -> list[ComplianceDocument]:
        """Every entry (current and history) of one vehicle and type."""
        result = await self.session.scalars(
            select(ComplianceDocument).where(
                ComplianceDocument.vehicle_id == vehicle_id, ComplianceDocument.document_type == document_type
            )
        )
        return list(result.all())

    async def count_same_document(self, vehicle_id: UUID, document_type: str) -> int:
        """Number of entries of one vehicle and type."""
        return (
            await self.session.scalar(
                select(func.count())
                .select_from(ComplianceDocument)
                .where(ComplianceDocument.vehicle_id == vehicle_id, ComplianceDocument.document_type == document_type)
            )
            or 0
        )

    async def successor_of(self, entry_id: UUID) -> ComplianceDocument | None:
        """The entry that was created by renewing ``entry_id``, if any."""
        return await self.session.scalar(
            select(ComplianceDocument).where(ComplianceDocument.renewed_from_id == entry_id).limit(1)
        )

    async def vehicle_options(self, q: str | None, limit: int = 20) -> list[Vehicle]:
        """Vehicles that can hold compliance documents, matched by plate, VIN, brand or model."""
        query = select(Vehicle).where(
            Vehicle.vehicle_type.in_([VehicleType(t) for t in sorted(COMPLIANCE_VEHICLE_TYPES)])
        )
        if q and q.strip():
            pattern = f"%{q.strip()}%"
            query = query.where(
                or_(
                    Vehicle.license_plate.ilike(pattern),
                    Vehicle.vin.ilike(pattern),
                    Vehicle.brand.ilike(pattern),
                    Vehicle.model.ilike(pattern),
                )
            )
        result = await self.session.scalars(query.order_by(Vehicle.license_plate).limit(limit))
        return list(result.all())

    async def lock_vehicle(self, vehicle_id: UUID) -> Vehicle | None:
        """Load a vehicle with FOR SHARE so a concurrent type change cannot interleave."""
        return await self.session.scalar(
            select(Vehicle)
            .where(Vehicle.id == vehicle_id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
