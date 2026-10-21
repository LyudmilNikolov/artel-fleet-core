"""Business rules for per-vehicle compliance documents (Документи и срокове).

Status is computed from end_date and never stored. Per (vehicle, type) the entry with
the latest end_date is current; older entries are history and never raise warnings.
"""

from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.compliance import ComplianceDocument
from app.models.document import Document, EntityType
from app.models.vehicle import Vehicle
from app.repositories.compliance_repo import STATUSES, ComplianceRepository
from app.schemas.compliance import ComplianceCreate, ComplianceRenew, ComplianceUpdate
from app.services import document_expense_sync as sync
from app.services.errors import DomainError
from app.services.validity import renewal_dates, utc_today, validity_status
from app.utils.s3 import get_s3_client
from app.vehicle_rules import compliance_types_for, validate_compliance_type


def compliance_status(end_date: date, today: date, window: int) -> str:
    """valid / expiring_soon / expired from an inclusive end date and an expiring-soon window in days."""
    return validity_status(end_date, today, window)


@dataclass
class ComplianceView:
    """An entry with its vehicle and computed, never-stored state."""

    entry: ComplianceDocument
    vehicle: Vehicle
    status: str
    days_left: int
    is_current: bool
    applicable: bool

    def __getattr__(self, name):
        return getattr(self.entry, name)


@dataclass
class VehicleComplianceSummary:
    """Per applicable type the current entry (or None when missing), plus every entry as history."""

    vehicle: Vehicle
    slots: list[tuple[str, ComplianceView | None]] = field(default_factory=list)
    entries: list[ComplianceView] = field(default_factory=list)


class ComplianceService:
    def __init__(self, session: AsyncSession, today: date | None = None, window: int | None = None):
        self.session = session
        self.repo = ComplianceRepository(session)
        self.today = today or utc_today()
        self.window = settings.COMPLIANCE_EXPIRING_SOON_DAYS if window is None else window

    def _view(self, row) -> ComplianceView:
        entry, vehicle, is_current, applicable = row
        return ComplianceView(
            entry=entry,
            vehicle=vehicle,
            status=compliance_status(entry.end_date, self.today, self.window),
            days_left=(entry.end_date - self.today).days,
            is_current=bool(is_current),
            applicable=bool(applicable),
        )

    async def _vehicle_for_write(self, vehicle_id: UUID, document_type: str) -> Vehicle:
        vehicle = await self.repo.lock_vehicle(vehicle_id)
        if not vehicle:
            raise DomainError("error.compliance_vehicle_not_found")
        try:
            validate_compliance_type(vehicle.vehicle_type, document_type)
        except ValueError as exc:
            raise DomainError(str(exc)) from exc
        return vehicle

    async def get(self, entry_id: UUID) -> ComplianceView | None:
        """Return one entry with computed status, or None."""
        row = await self.repo.get_row(entry_id)
        return self._view(row) if row else None

    async def _require(self, entry_id: UUID) -> ComplianceView:
        view = await self.get(entry_id)
        if not view:
            raise DomainError("error.compliance_not_found")
        return view

    async def list_entries(
        self,
        *,
        vehicle_id: UUID | None = None,
        q: str | None = None,
        document_type: str | None = None,
        status: str | None = None,
        current_only: bool = True,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[ComplianceView], int]:
        """List entries (current ones by default), most urgent end date first."""
        if status is not None and status not in STATUSES:
            raise DomainError("errors.invalid_input")
        rows, total = await self.repo.search(
            today=self.today,
            window=self.window,
            vehicle_id=vehicle_id,
            q=q,
            document_type=document_type,
            status=status,
            current_only=current_only,
            offset=(page - 1) * size,
            limit=size,
        )
        return [self._view(row) for row in rows], total

    async def create(self, data: ComplianceCreate, user_id: UUID | None) -> ComplianceView:
        """Create an entry after checking the type applies to the (locked) vehicle."""
        await self._vehicle_for_write(data.vehicle_id, data.document_type)
        entry = await self.repo.create(**data.model_dump(), created_by=user_id)
        await sync.sync_from_document(self.session, entry, user_id)
        return await self._require(entry.id)

    async def renew(self, source_id: UUID, data: ComplianceRenew, user_id: UUID | None) -> ComplianceView:
        """Create a new entry for the same vehicle and type; the source stays unchanged as history.

        The new start may precede the source's end (policies are often bought early).
        """
        source = await self._require(source_id)
        await self._vehicle_for_write(source.vehicle_id, source.document_type)
        entry = await self.repo.create(
            **data.model_dump(),
            vehicle_id=source.vehicle_id,
            document_type=source.document_type,
            renewed_from_id=source.id,
            created_by=user_id,
        )
        await sync.sync_from_document(self.session, entry, user_id)
        return await self._require(entry.id)

    def renewal_proposal(self, source: ComplianceView) -> dict:
        """Prefill values for a renewal form: same provider, next period of equal length."""
        start, end = renewal_dates(source.start_date, source.end_date)
        return {"start_date": start, "end_date": end, "provider": source.provider}

    async def update(self, entry_id: UUID, data: ComplianceUpdate) -> ComplianceView:
        """Change dates, amount, provider, policy number or notes; vehicle and type are fixed."""
        view = await self._require(entry_id)
        values = data.model_dump(exclude_unset=True)
        start = values.get("start_date", view.start_date)
        end = values.get("end_date", view.end_date)
        if end < start:
            raise DomainError("error.compliance_end_before_start")
        await self.repo.update(view.entry, **values)
        if "amount" in values or "provider" in values:
            await sync.sync_from_document(self.session, view.entry, view.entry.created_by)
        return await self._require(entry_id)

    async def history_count(self, entry_id: UUID) -> int:
        """Number of entries for the same vehicle and type, including this one."""
        entry = await self.repo.get_by_id(entry_id)
        if not entry:
            raise DomainError("error.compliance_not_found")
        return await self.repo.count_same_document(entry.vehicle_id, entry.document_type)

    async def delete(self, entry_id: UUID, scope: str = "entry") -> int:
        """Delete one entry (scope "entry") or every entry of the same vehicle and type (scope "all").

        Attachments and stored files go with the deleted entries. Deleting only the current
        entry makes the previous one current again. Returns the number of deleted entries.
        """
        if scope not in ("entry", "all"):
            raise DomainError("errors.invalid_input")
        entry = await self.repo.get_by_id(entry_id)
        if not entry:
            raise DomainError("error.compliance_not_found")
        entries = await self.repo.same_document(entry.vehicle_id, entry.document_type) if scope == "all" else [entry]
        ids = [e.id for e in entries]
        documents = (
            await self.session.scalars(
                select(Document).where(
                    Document.entity_type == EntityType.COMPLIANCE_DOCUMENT, Document.entity_id.in_(ids)
                )
            )
        ).all()
        keys = [doc.s3_key for doc in documents]
        for doc in documents:
            await self.session.delete(doc)
        # Mirror ON DELETE SET NULL in loaded objects so surviving renewals stay consistent in this session.
        survivors = await self.session.scalars(
            select(ComplianceDocument).where(
                ComplianceDocument.renewed_from_id.in_(ids), ComplianceDocument.id.not_in(ids)
            )
        )
        for survivor in survivors.all():
            survivor.renewed_from_id = None
        for item in entries:
            await self.repo.delete(item)
        if keys:
            s3 = get_s3_client()
            for key in keys:
                try:
                    s3.delete_file(key)
                except Exception:  # noqa: BLE001 - rows are already gone; do not fail the delete
                    pass
        return len(entries)

    async def summary_for_vehicle(self, vehicle: Vehicle) -> VehicleComplianceSummary:
        """Current entry (or missing) per applicable type, plus the full history."""
        views = [self._view(row) for row in await self.repo.for_vehicle(vehicle.id)]
        current = {v.document_type: v for v in views if v.is_current}
        slots = [(t, current.get(t)) for t in compliance_types_for(vehicle.vehicle_type)]
        return VehicleComplianceSummary(vehicle=vehicle, slots=slots, entries=views)

    async def vehicle_options(self, q: str | None, limit: int = 20) -> list[Vehicle]:
        """Vehicles offered by the picker: only types that have applicable documents."""
        return await self.repo.vehicle_options(q, limit)

    async def allowed_types(self, vehicle_id: UUID) -> tuple[str, ...]:
        """Compliance types that apply to a vehicle's current type."""
        vehicle = await self.session.get(Vehicle, vehicle_id)
        if not vehicle:
            raise DomainError("error.compliance_vehicle_not_found")
        return compliance_types_for(vehicle.vehicle_type)

    async def attention_counts(self) -> dict[str, int]:
        """Current, applicable entries expiring soon or expired (decommissioned vehicles excluded)."""
        return {
            status: await self.repo.count_current(today=self.today, window=self.window, status=status)
            for status in ("expiring_soon", "expired")
        }

    async def successor(self, entry_id: UUID) -> ComplianceDocument | None:
        """The renewal created from an entry, if any."""
        return await self.repo.successor_of(entry_id)
