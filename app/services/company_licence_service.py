"""Business logic for the company licence: one validity period per entry, the latest end date is current."""

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.company_licence import CompanyLicence
from app.models.document import Document, EntityType
from app.schemas.company_licence import CompanyLicenceInput
from app.services.errors import DomainError
from app.services.validity import local_today, renewal_dates, validity_status
from app.utils.s3 import get_s3_client


@dataclass(frozen=True)
class CompanyLicenceView:
    """A stored entry with its computed (never stored) status."""

    entry: CompanyLicence
    status: str  # valid | expiring_soon | expired
    days_left: int  # negative once expired
    is_current: bool

    def __getattr__(self, name):  # templates read the entry's columns directly
        return getattr(self.entry, name)


class CompanyLicenceService:
    def __init__(self, session: AsyncSession, today: date | None = None):
        self.session = session
        self.today = today or local_today()

    def _view(self, entry: CompanyLicence, current_id: UUID | None) -> CompanyLicenceView:
        return CompanyLicenceView(
            entry=entry,
            status=validity_status(entry.end_date, self.today, settings.COMPLIANCE_EXPIRING_SOON_DAYS),
            days_left=(entry.end_date - self.today).days,
            is_current=entry.id == current_id,
        )

    async def _entries(self) -> list[CompanyLicence]:
        """Every entry, current first (latest end date; ties: latest start, newest created)."""
        rows = await self.session.scalars(
            select(CompanyLicence).order_by(
                CompanyLicence.end_date.desc(), CompanyLicence.start_date.desc(), CompanyLicence.created_at.desc()
            )
        )
        return list(rows.all())

    async def list_entries(self) -> list[CompanyLicenceView]:
        """All entries, the current one first; older ones are history and never warn."""
        entries = await self._entries()
        current_id = entries[0].id if entries else None
        return [self._view(entry, current_id) for entry in entries]

    async def current(self) -> CompanyLicenceView | None:
        """The current entry, or None when no licence has been entered."""
        entries = await self.list_entries()
        return entries[0] if entries else None

    async def get(self, entry_id: UUID) -> CompanyLicenceView | None:
        """One entry with its status, or None."""
        entry = await self.session.get(CompanyLicence, entry_id)
        if entry is None:
            return None
        entries = await self._entries()
        return self._view(entry, entries[0].id if entries else None)

    async def alert(self) -> CompanyLicenceView | None:
        """The current entry when it is expired or expiring soon (for the dashboard), otherwise None."""
        current = await self.current()
        return current if current and current.status in ("expired", "expiring_soon") else None

    async def create(self, data: CompanyLicenceInput, user_id: UUID | None, renewed_from_id: UUID | None = None):
        """Add an entry. ``renewed_from_id`` marks it as the renewal of an earlier entry, which stays as history."""
        if renewed_from_id is not None and await self.session.get(CompanyLicence, renewed_from_id) is None:
            raise DomainError("error.company_licence_not_found")
        entry = CompanyLicence(**data.model_dump(), renewed_from_id=renewed_from_id, created_by=user_id)
        self.session.add(entry)
        await self.session.flush()
        return await self.get(entry.id)

    async def update(self, entry_id: UUID, data: CompanyLicenceInput) -> CompanyLicenceView:
        """Correct an entry (use renew for a new period)."""
        entry = await self.session.get(CompanyLicence, entry_id)
        if entry is None:
            raise DomainError("error.company_licence_not_found")
        for name, value in data.model_dump().items():
            setattr(entry, name, value)
        await self.session.flush()
        return await self.get(entry_id)

    def renewal_proposal(self, source: CompanyLicenceView) -> dict:
        """Form values for the next period: number and holder kept, dates proposed after the old end."""
        start, end = renewal_dates(source.start_date, source.end_date)
        return {
            "licence_number": source.licence_number,
            "holder": source.holder,
            "issued_by": source.issued_by,
            "start_date": start,
            "end_date": end,
        }

    async def delete(self, entry_id: UUID) -> None:
        """Delete one entry; its scans and stored files go with it. The previous entry becomes current again."""
        entry = await self.session.get(CompanyLicence, entry_id)
        if entry is None:
            raise DomainError("error.company_licence_not_found")
        documents = (
            await self.session.scalars(
                select(Document).where(Document.entity_type == EntityType.COMPANY_LICENCE, Document.entity_id == entry_id)
            )
        ).all()
        keys = [doc.s3_key for doc in documents]
        for doc in documents:
            await self.session.delete(doc)
        survivors = await self.session.scalars(select(CompanyLicence).where(CompanyLicence.renewed_from_id == entry_id))
        for survivor in survivors.all():  # mirror ON DELETE SET NULL for loaded objects
            survivor.renewed_from_id = None
        await self.session.delete(entry)
        await self.session.flush()
        if keys:
            s3 = get_s3_client()
            for key in keys:
                try:
                    s3.delete_file(key)
                except Exception:  # noqa: BLE001 - rows are already gone; do not fail the delete
                    pass
