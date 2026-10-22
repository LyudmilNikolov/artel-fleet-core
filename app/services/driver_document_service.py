"""Business rules for per-driver tracked documents (Лицензи и документи).

Status is computed from end_date and never stored; a missing end date means the document
does not expire. Per (driver, type) the entry with the latest end date is current (no end date
counts as latest, and a renewed entry is never current). Older entries are history and never
raise warnings.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.driver_document_rules import DRIVER_DOCUMENT_TYPES, mask_number, validate_driver_document
from app.models.audit_log import AuditAction
from app.models.document import Document, EntityType
from app.models.driver import Driver
from app.models.driver_document import DriverDocument
from app.repositories.driver_document_repo import ALERT_STATUSES, STATUSES, DriverDocumentRepository
from app.schemas.driver_document import DriverDocumentCreate, DriverDocumentRenew, DriverDocumentUpdate
from app.services.audit_service import AuditService
from app.services.errors import DomainError
from app.services.validity import local_today, renewal_dates, validity_status
from app.utils.s3 import get_s3_client

AUDITED_FIELDS = ("document_type", "document_number", "categories", "start_date", "end_date", "notes")


def _audit_value(name: str, value):
    """JSON-safe audit value; document numbers are stored masked (the log is append-only)."""
    if name == "document_number":
        return mask_number(value)
    return value.isoformat() if isinstance(value, date) else value


@dataclass
class DriverDocumentView:
    """An entry with its driver and computed, never-stored state."""

    entry: DriverDocument
    driver: Driver
    status: str
    days_left: int | None
    is_current: bool

    def __getattr__(self, name):
        return getattr(self.entry, name)


@dataclass
class DriverDocumentSummary:
    """Per type the current entry (or None when missing), plus every entry as history."""

    driver: Driver
    slots: list[tuple[str, DriverDocumentView | None]] = field(default_factory=list)
    entries: list[DriverDocumentView] = field(default_factory=list)


class DriverDocumentService:
    def __init__(self, session: AsyncSession, today: date | None = None, window: int | None = None):
        self.session = session
        self.repo = DriverDocumentRepository(session)
        self.audit = AuditService(session)
        self.today = today or local_today()
        self.window = settings.DRIVER_DOCUMENT_EXPIRING_SOON_DAYS if window is None else window

    def _view(self, row) -> DriverDocumentView:
        entry, driver, is_current = row
        return DriverDocumentView(
            entry=entry,
            driver=driver,
            status=validity_status(entry.end_date, self.today, self.window),
            days_left=(entry.end_date - self.today).days if entry.end_date else None,
            is_current=bool(is_current),
        )

    async def _driver_for_write(self, driver_id: UUID) -> Driver:
        driver = await self.repo.lock_driver(driver_id)
        if not driver:
            raise DomainError("error.driver_not_found")
        return driver

    @staticmethod
    def _validate(document_type: str, number: str | None, categories: str | None) -> None:
        try:
            validate_driver_document(document_type, number, categories)
        except ValueError as exc:
            raise DomainError(str(exc)) from exc

    async def get(self, entry_id: UUID) -> DriverDocumentView | None:
        """Return one entry with computed status, or None."""
        row = await self.repo.get_row(entry_id)
        return self._view(row) if row else None

    async def _require(self, entry_id: UUID) -> DriverDocumentView:
        view = await self.get(entry_id)
        if not view:
            raise DomainError("error.driver_document_not_found")
        return view

    async def list_entries(
        self,
        *,
        driver_id: UUID | None = None,
        document_type: str | None = None,
        status: str | None = None,
        current_only: bool = True,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[DriverDocumentView], int]:
        """List entries (current ones by default), most urgent end date first, no-expiry last."""
        if status is not None and status not in STATUSES:
            raise DomainError("errors.invalid_input")
        if document_type is not None and document_type not in DRIVER_DOCUMENT_TYPES:
            raise DomainError("error.driver_document_type_unknown")
        rows, total = await self.repo.search(
            today=self.today,
            window=self.window,
            driver_id=driver_id,
            document_type=document_type,
            status=status,
            current_only=current_only,
            offset=(page - 1) * size,
            limit=size,
        )
        return [self._view(row) for row in rows], total

    async def create(self, data: DriverDocumentCreate, user_id: UUID | None) -> DriverDocumentView:
        """Create an entry after checking the per-type rules on the (locked) driver."""
        await self._driver_for_write(data.driver_id)
        self._validate(data.document_type, data.document_number, data.categories)
        entry = await self.repo.create(**data.model_dump(), created_by=user_id)
        await self._log(user_id, AuditAction.CREATE, entry, {n: {"old": None, "new": _audit_value(n, getattr(entry, n))} for n in AUDITED_FIELDS})
        return await self._require(entry.id)

    async def renew(self, source_id: UUID, data: DriverDocumentRenew, user_id: UUID | None) -> DriverDocumentView:
        """Create a new entry for the same driver and type; the source stays unchanged as history.

        Number and categories not sent are inherited from the source. The new start may precede
        the source's end (documents are often renewed early).
        """
        source = await self._require(source_id)
        await self._driver_for_write(source.driver_id)
        values = data.model_dump()
        for name in ("document_number", "categories"):
            if name not in data.model_fields_set:
                values[name] = getattr(source, name)
        self._validate(source.document_type, values["document_number"], values["categories"])
        entry = await self.repo.create(
            **values,
            driver_id=source.driver_id,
            document_type=source.document_type,
            renewed_from_id=source.id,
            created_by=user_id,
        )
        changes = {n: {"old": None, "new": _audit_value(n, getattr(entry, n))} for n in AUDITED_FIELDS}
        changes["renewed_from_id"] = {"old": None, "new": str(source.id)}
        await self._log(user_id, AuditAction.CREATE, entry, changes)
        return await self._require(entry.id)

    def renewal_proposal(self, source: DriverDocumentView) -> dict:
        """Prefill values for a renewal form: same number and categories, next period of equal length."""
        start: date | None
        end: date | None
        if source.start_date and source.end_date:
            start, end = renewal_dates(source.start_date, source.end_date)
        elif source.end_date:
            start, end = source.end_date + timedelta(days=1), None
        else:
            start, end = self.today, None
        return {
            "start_date": start,
            "end_date": end,
            "document_number": source.document_number,
            "categories": source.categories,
            "notes": source.notes,
        }

    async def update(
        self, entry_id: UUID, data: DriverDocumentUpdate, user_id: UUID | None = None
    ) -> DriverDocumentView:
        """Change number, categories, dates or notes; driver and type are fixed."""
        view = await self._require(entry_id)
        values = data.model_dump(exclude_unset=True)
        start = values.get("start_date", view.start_date)
        end = values.get("end_date", view.end_date)
        if start and end and end < start:
            raise DomainError("error.driver_document_end_before_start")
        self._validate(
            view.document_type,
            values.get("document_number", view.document_number),
            values.get("categories", view.categories),
        )
        old = {n: getattr(view.entry, n) for n in AUDITED_FIELDS}
        await self.repo.update(view.entry, **values)
        new = {n: getattr(view.entry, n) for n in AUDITED_FIELDS}
        # Diff raw values (a masked number would hide a changed prefix), then mask what is stored.
        changes = {
            name: {"old": _audit_value(name, diff["old"]), "new": _audit_value(name, diff["new"])}
            for name, diff in AuditService.compute_diff(old, new).items()
        }
        if changes:
            await self._log(user_id, AuditAction.UPDATE, view.entry, changes)
        return await self._require(entry_id)

    async def history_count(self, entry_id: UUID) -> int:
        """Number of entries for the same driver and type, including this one."""
        entry = await self.repo.get_by_id(entry_id)
        if not entry:
            raise DomainError("error.driver_document_not_found")
        return await self.repo.count_same_document(entry.driver_id, entry.document_type)

    async def delete(self, entry_id: UUID, scope: str = "entry", user_id: UUID | None = None) -> int:
        """Delete one entry (scope "entry") or every entry of the same driver and type (scope "all").

        Attachments and stored files go with the deleted entries. Deleting only the current entry
        makes the previous one current again. Returns the number of deleted entries.
        """
        if scope not in ("entry", "all"):
            raise DomainError("errors.invalid_input")
        entry = await self.repo.get_by_id(entry_id)
        if not entry:
            raise DomainError("error.driver_document_not_found")
        entries = await self.repo.same_document(entry.driver_id, entry.document_type) if scope == "all" else [entry]
        ids = [e.id for e in entries]
        documents = (
            await self.session.scalars(
                select(Document).where(Document.entity_type == EntityType.DRIVER_DOCUMENT, Document.entity_id.in_(ids))
            )
        ).all()
        keys = [doc.s3_key for doc in documents]
        for doc in documents:
            await self.session.delete(doc)
        # Mirror ON DELETE SET NULL in loaded objects so surviving renewals stay consistent in this session.
        survivors = await self.session.scalars(
            select(DriverDocument).where(DriverDocument.renewed_from_id.in_(ids), DriverDocument.id.not_in(ids))
        )
        for survivor in survivors.all():
            survivor.renewed_from_id = None
        for item in entries:
            await self._log(
                user_id,
                AuditAction.DELETE,
                item,
                {n: {"old": _audit_value(n, getattr(item, n)), "new": None} for n in AUDITED_FIELDS},
            )
            await self.repo.delete(item)
        if keys:
            s3 = get_s3_client()
            for key in keys:
                try:
                    s3.delete_file(key)
                except Exception:  # noqa: BLE001 - rows are already gone; do not fail the delete
                    pass
        return len(entries)

    async def summary_for_driver(self, driver: Driver) -> DriverDocumentSummary:
        """Current entry (or missing) per type in display order, plus the full history."""
        views = [self._view(row) for row in await self.repo.for_driver(driver.id)]
        current = {v.document_type: v for v in views if v.is_current}
        slots = [(t, current.get(t)) for t in DRIVER_DOCUMENT_TYPES]
        return DriverDocumentSummary(driver=driver, slots=slots, entries=views)

    async def alert_counts_by_driver(self, driver_ids: list[UUID]) -> dict[UUID, dict[str, int]]:
        """``{driver_id: {"expired": n, "expiring_soon": n}}`` for current entries, one query per status."""
        return await self.repo.alert_counts(driver_ids, today=self.today, window=self.window)

    async def attention_counts(self) -> dict[str, int]:
        """Current entries of active drivers that are expiring soon or expired."""
        return {
            status: await self.repo.count_current(today=self.today, window=self.window, status=status)
            for status in ALERT_STATUSES
        }

    async def successor(self, entry_id: UUID) -> DriverDocument | None:
        """The renewal created from an entry, if any."""
        return await self.repo.successor_of(entry_id)

    async def _log(self, user_id: UUID | None, action: AuditAction, entry: DriverDocument, changes: dict) -> None:
        """Append an audit entry; numbers inside ``changes`` are already masked."""
        await self.audit.log(
            user_id=user_id,
            action=action,
            entity_type="driver_document",
            entity_id=entry.id,
            changes={"driver_id": str(entry.driver_id), **changes},
        )
