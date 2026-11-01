"""Approval workflow for driver-proposed document changes.

A driver never writes a tracked document directly. They *submit* a change (new document, edit or renewal);
it stays ``pending`` and has no effect on the driver's documents or on expiry alerts until a manager
*approves* it, which applies it through ``DriverDocumentService``. A manager can also

- *request changes* (``revision``): the request stays visible to the driver with the manager's note; the driver
  corrects and *resubmits* it, or deletes it;
- *reject* it: the request is deleted and not shown again (only the append-only audit log keeps a record).

The driver can delete their own request at any time while it is pending or waiting for changes.
Managers keep editing documents directly.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.i18n import _
from app.models.audit_log import AuditAction
from app.models.driver import Driver
from app.models.driver_document import DriverDocument
from app.models.driver_document_change import ChangeAction, ChangeStatus, DriverDocumentChange
from app.models.notification import Notification, NotificationType
from app.models.user import User, UserRole
from app.schemas.driver_document import DriverDocumentCreate, DriverDocumentRenew, DriverDocumentUpdate
from app.services.audit_service import AuditService
from app.services.driver_document_service import AUDITED_FIELDS, DriverDocumentService, _audit_value
from app.services.errors import DomainError

PAYLOAD_FIELDS = ("document_number", "categories", "start_date", "end_date", "notes")
MAX_NOTE = 500
RECENT_REJECTED = 5


@dataclass
class ChangeView:
    """A change with the people and the entry it refers to (the entry as it is now, for edits and renewals)."""

    change: DriverDocumentChange
    driver: Driver
    entry: DriverDocument | None
    requester: User | None
    decider: User | None = None

    def __getattr__(self, name):
        return getattr(self.change, name)

    def differences(self) -> list[tuple[str, object, object]]:
        """(field, current value, proposed value) for the fields that differ; current is None for a new document."""
        rows = []
        for name in PAYLOAD_FIELDS:
            proposed = self.change.payload.get(name)
            current = None
            if self.entry is not None:
                current = getattr(self.entry, name)
                current = current.isoformat() if isinstance(current, date) else current
            if self.change.action == ChangeAction.CREATE.value:
                if proposed:  # a new document shows only what was filled in
                    rows.append((name, None, proposed))
            elif current != proposed:
                rows.append((name, current, proposed))
        return rows


class DriverDocumentChangeService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.docs = DriverDocumentService(session)
        self.audit = AuditService(session)

    # --- driver side ---------------------------------------------------------------------------------

    async def submit(
        self,
        *,
        action: ChangeAction,
        driver_id: UUID,
        requested_by: UUID,
        values: dict,
        document_type: str | None = None,
        entry_id: UUID | None = None,
    ) -> DriverDocumentChange:
        """Validate a proposed change exactly like a direct write would, then store it as pending.

        ``values`` are the five document fields as submitted (pydantic ``ValidationError`` and ``DomainError``
        propagate to the caller, which shows them on the form).
        """
        driver = await self.session.get(Driver, driver_id)
        if driver is None:
            raise DomainError("error.driver_not_found")
        document_type, payload, entry = await self._prepare(action, driver_id, document_type, entry_id, values)
        if await self._pending_for(driver_id, document_type) is not None:
            raise DomainError("error.driver_document_change_pending")
        # a fresh proposal replaces any earlier request for changes on the same document type
        for old in await self._revisions_for(driver_id, document_type):
            await self.session.delete(old)
        change = DriverDocumentChange(
            driver_id=driver_id,
            entry_id=entry.id if entry else None,
            action=action.value,
            document_type=document_type,
            payload=payload,
            status=ChangeStatus.PENDING.value,
            requested_by=requested_by,
        )
        self.session.add(change)
        await self.session.flush()
        await self._log(requested_by, AuditAction.CREATE, change, driver, self._masked(payload))
        await self._notify_managers(change, driver)
        return change

    async def resubmit(self, change_id: UUID, user_id: UUID, values: dict) -> DriverDocumentChange:
        """The driver corrects a request the manager sent back; it becomes pending again for the same entry/type."""
        change = await self._revision(change_id)
        if change.requested_by != user_id:
            raise DomainError("errors.forbidden")
        document_type, payload, _ = await self._prepare(
            ChangeAction(change.action), change.driver_id, change.document_type, change.entry_id, values
        )
        if await self._pending_for(change.driver_id, document_type) is not None:
            raise DomainError("error.driver_document_change_pending")
        change.payload = payload
        change.status = ChangeStatus.PENDING.value
        change.decided_by = None
        change.decided_at = None
        change.decision_note = None
        await self.session.flush()
        driver = await self.session.get(Driver, change.driver_id)
        await self._log(
            user_id,
            AuditAction.UPDATE,
            change,
            None,
            {"status": {"old": "revision", "new": "pending"}, **self._masked(payload)},
        )
        await self._notify_managers(change, driver)
        return change

    async def cancel(self, change_id: UUID, user_id: UUID) -> None:
        """The requester deletes their own request (pending or sent back); it is not shown again."""
        change = await self._open(change_id)
        if change.requested_by != user_id:
            raise DomainError("errors.forbidden")
        await self._log(user_id, AuditAction.DELETE, change, None, {"status": {"old": change.status, "new": "deleted"}})
        await self.session.delete(change)
        await self.session.flush()

    # --- manager side --------------------------------------------------------------------------------

    async def approve(self, change_id: UUID, approver_id: UUID) -> DriverDocumentChange:
        """Apply the change to the driver's documents and close it. Rules are checked again at this point."""
        change = await self._pending(change_id)
        payload = change.payload
        if change.action == ChangeAction.CREATE.value:
            data = DriverDocumentCreate(driver_id=change.driver_id, document_type=change.document_type, **payload)
            view = await self.docs.create(data, approver_id)
        elif change.action == ChangeAction.EDIT.value:
            view = await self.docs.update(change.entry_id, DriverDocumentUpdate(**payload), approver_id)
        else:
            view = await self.docs.renew(change.entry_id, DriverDocumentRenew(**payload), approver_id)
        change.status = ChangeStatus.APPROVED.value
        change.decided_by = approver_id
        change.decided_at = datetime.now(UTC)
        change.result_entry_id = view.id
        await self.session.flush()
        await self._log(
            approver_id, AuditAction.UPDATE, change, None, {"status": {"old": "pending", "new": "approved"}}
        )
        await self._notify_requester(change, "approved")
        return change

    async def request_changes(
        self, change_id: UUID, approver_id: UUID, note: str | None = None
    ) -> DriverDocumentChange:
        """Send the request back: the driver sees the note and can correct and resubmit it (or delete it).

        The note is required: the driver has to be told what to fix.
        """
        change = await self._pending(change_id)
        note = (note or "").strip()[:MAX_NOTE]
        if not note:
            raise DomainError("error.driver_doc_note_required")
        change.status = ChangeStatus.REVISION.value
        change.decided_by = approver_id
        change.decided_at = datetime.now(UTC)
        change.decision_note = note
        await self.session.flush()
        await self._log(
            approver_id, AuditAction.UPDATE, change, None, {"status": {"old": "pending", "new": "revision"}}
        )
        await self._notify_requester(change, "revision")
        return change

    async def reject(self, change_id: UUID, approver_id: UUID) -> None:
        """Refuse the request: the driver is told it was rejected, then it is deleted and not shown again."""
        change = await self._pending(change_id)
        await self._log(
            approver_id, AuditAction.DELETE, change, None, {"status": {"old": "pending", "new": "rejected"}}
        )
        await self._notify_requester(change, "rejected")
        await self.session.delete(change)
        await self.session.flush()

    # --- queries -------------------------------------------------------------------------------------

    async def pending_count(self) -> int:
        return (
            await self.session.scalar(
                select(func.count()).select_from(DriverDocumentChange).where(DriverDocumentChange.status == "pending")
            )
            or 0
        )

    async def list_pending(self, driver_id: UUID | None = None) -> list[ChangeView]:
        """Pending changes, oldest first (optionally one driver's)."""
        conditions = [DriverDocumentChange.status == ChangeStatus.PENDING.value]
        if driver_id:
            conditions.append(DriverDocumentChange.driver_id == driver_id)
        return await self._views(
            select(DriverDocumentChange)
            .where(*conditions)
            .order_by(DriverDocumentChange.created_at, DriverDocumentChange.id)
        )

    async def list_revisions(self, driver_id: UUID) -> list[ChangeView]:
        """Requests the manager sent back to this driver (with the manager's note)."""
        return await self._views(
            select(DriverDocumentChange)
            .where(
                DriverDocumentChange.driver_id == driver_id, DriverDocumentChange.status == ChangeStatus.REVISION.value
            )
            .order_by(DriverDocumentChange.decided_at.desc())
        )

    async def get(self, change_id: UUID) -> DriverDocumentChange | None:
        return await self.session.get(DriverDocumentChange, change_id)

    async def pending_for_entry(self, entry_id: UUID) -> DriverDocumentChange | None:
        return await self.session.scalar(
            select(DriverDocumentChange).where(
                DriverDocumentChange.entry_id == entry_id, DriverDocumentChange.status == "pending"
            )
        )

    # --- internals -----------------------------------------------------------------------------------

    async def _views(self, query) -> list[ChangeView]:
        changes = list((await self.session.scalars(query)).all())
        if not changes:
            return []
        drivers = {
            d.id: d
            for d in await self.session.scalars(select(Driver).where(Driver.id.in_({c.driver_id for c in changes})))
        }
        entry_ids = {c.entry_id for c in changes if c.entry_id}
        entries = (
            {
                e.id: e
                for e in await self.session.scalars(select(DriverDocument).where(DriverDocument.id.in_(entry_ids)))
            }
            if entry_ids
            else {}
        )
        user_ids = {u for c in changes for u in (c.requested_by, c.decided_by) if u}
        users = (
            {u.id: u for u in await self.session.scalars(select(User).where(User.id.in_(user_ids)))} if user_ids else {}
        )
        return [
            ChangeView(
                c, drivers[c.driver_id], entries.get(c.entry_id), users.get(c.requested_by), users.get(c.decided_by)
            )
            for c in changes
        ]

    async def _prepare(self, action: ChangeAction, driver_id: UUID, document_type, entry_id, values: dict):
        """Check a proposal exactly like a direct write would. Returns (document type, payload, entry or None)."""
        entry = None
        if action == ChangeAction.CREATE:
            data = DriverDocumentCreate(driver_id=driver_id, document_type=document_type, **values)
            document_type = data.document_type.value if hasattr(data.document_type, "value") else data.document_type
            self.docs._validate(document_type, data.document_number, data.categories)
            payload = data.model_dump(mode="json", include=set(PAYLOAD_FIELDS))
        else:
            view = await self.docs.get(entry_id) if entry_id else None
            if view is None or view.driver_id != driver_id:
                raise DomainError("error.driver_document_not_found")
            entry = view.entry
            document_type = view.document_type
            schema = DriverDocumentUpdate if action == ChangeAction.EDIT else DriverDocumentRenew
            data = schema(**values)
            self.docs._validate(document_type, data.document_number, data.categories)
            payload = data.model_dump(mode="json", include=set(PAYLOAD_FIELDS))
            if action == ChangeAction.EDIT and all(
                payload[name] == _iso(getattr(entry, name)) for name in PAYLOAD_FIELDS
            ):
                raise DomainError("error.driver_document_change_none")
        return document_type, payload, entry

    async def _open(self, change_id: UUID) -> DriverDocumentChange:
        """A request that is still open: pending or sent back."""
        change = await self.session.get(DriverDocumentChange, change_id)
        if change is None:
            raise DomainError("error.driver_document_change_not_found")
        if change.status not in (ChangeStatus.PENDING.value, ChangeStatus.REVISION.value):
            raise DomainError("error.driver_document_change_closed")
        return change

    async def _revision(self, change_id: UUID) -> DriverDocumentChange:
        change = await self._open(change_id)
        if change.status != ChangeStatus.REVISION.value:
            raise DomainError("error.driver_document_change_closed")
        return change

    async def _revisions_for(self, driver_id: UUID, document_type: str) -> list[DriverDocumentChange]:
        rows = await self.session.scalars(
            select(DriverDocumentChange).where(
                DriverDocumentChange.driver_id == driver_id,
                DriverDocumentChange.document_type == document_type,
                DriverDocumentChange.status == ChangeStatus.REVISION.value,
            )
        )
        return list(rows.all())

    async def _pending(self, change_id: UUID) -> DriverDocumentChange:
        change = await self.session.get(DriverDocumentChange, change_id)
        if change is None:
            raise DomainError("error.driver_document_change_not_found")
        if change.status != ChangeStatus.PENDING.value:
            raise DomainError("error.driver_document_change_closed")
        return change

    async def _pending_for(self, driver_id: UUID, document_type: str) -> DriverDocumentChange | None:
        return await self.session.scalar(
            select(DriverDocumentChange).where(
                DriverDocumentChange.driver_id == driver_id,
                DriverDocumentChange.document_type == document_type,
                DriverDocumentChange.status == "pending",
            )
        )

    @staticmethod
    def _masked(payload: dict) -> dict:
        """Audit values: numbers masked (the log is append-only)."""
        return {
            name: {"old": None, "new": _audit_value(name, payload.get(name))}
            for name in AUDITED_FIELDS
            if name in payload
        }

    async def _log(self, user_id, action: AuditAction, change, driver: Driver | None, changes: dict) -> None:
        label = f"{_('driver_document_type.' + change.document_type, 'en')}: {change.action}"
        await self.audit.log(
            user_id=user_id,
            action=action,
            entity_type="driver_document_change",
            entity_id=change.id,
            changes={"_label": label, "driver_id": str(change.driver_id), "action": change.action, **changes},
        )

    async def _notify_managers(self, change: DriverDocumentChange, driver: Driver) -> None:
        managers = await self.session.scalars(
            select(User).where(User.is_active.is_(True), User.role.in_([UserRole.ADMIN, UserRole.FLEET_MANAGER]))
        )
        for manager in managers.all():
            lang = manager.language or "bg"
            self.session.add(
                Notification(
                    user_id=manager.id,
                    title=_("driver_doc.notify_pending_title", lang),
                    message=_(
                        "driver_doc.notify_pending_message",
                        lang,
                        driver=driver.full_name,
                        type=_("driver_document_type." + change.document_type, lang),
                    ),
                    type=NotificationType.SYSTEM,
                    title_key="driver_doc.notify_pending_title",
                    message_key="driver_doc.notify_pending_message",
                    params={"driver": driver.full_name, "type": {"t": "driver_document_type." + change.document_type}},
                )
            )
        await self.session.flush()

    async def _notify_requester(self, change: DriverDocumentChange, outcome: str) -> None:
        user = await self.session.get(User, change.requested_by) if change.requested_by else None
        if user is None:
            return
        lang = user.language or "bg"
        message = _(
            f"driver_doc.notify_{outcome}_message", lang, type=_("driver_document_type." + change.document_type, lang)
        )
        if change.decision_note:
            message += " " + change.decision_note
        self.session.add(
            Notification(
                user_id=user.id,
                title=_(f"driver_doc.notify_{outcome}_title", lang),
                message=message,
                type=NotificationType.SYSTEM,
                title_key=f"driver_doc.notify_{outcome}_title",
                message_key=f"driver_doc.notify_{outcome}_message",
                params={
                    "type": {"t": "driver_document_type." + change.document_type},
                    **({"note": change.decision_note} if change.decision_note else {}),
                },
            )
        )
        await self.session.flush()


def _iso(value):
    return value.isoformat() if isinstance(value, date) else value
