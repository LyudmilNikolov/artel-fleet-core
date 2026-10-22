"""Stop / Resume of reminders for one document (global: every recipient, every channel).

A stop stores the document's end date and only applies while the document still has it, so renewing the document
or correcting its end date starts reminders again without any extra step. Both actions are audited.
"""

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditAction
from app.models.notification_dispatch import NotificationStop
from app.models.user import User, UserRole
from app.notification_rules import DRIVER_ENTITY, VEHICLE_ENTITY
from app.services.audit_service import AuditService
from app.services.errors import DomainError

ENTITY_TYPES = (VEHICLE_ENTITY, DRIVER_ENTITY)
MAY_STOP = (UserRole.ADMIN, UserRole.FLEET_MANAGER)


@dataclass(frozen=True)
class StopInfo:
    stopped_by_name: str | None
    stopped_at: datetime


class ReminderStopService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.audit = AuditService(session)

    def _check(self, entity_type: str, user: User) -> None:
        if user.role not in MAY_STOP:
            raise DomainError("errors.forbidden")
        if entity_type not in ENTITY_TYPES:
            raise DomainError("errors.invalid_input")

    async def active(self, entity_type: str, entity_id: UUID, end_date: date) -> StopInfo | None:
        """The stop that applies to this document entry (same end date, not resumed), if any."""
        row = (
            await self.session.execute(
                select(NotificationStop, User.full_name)
                .outerjoin(User, User.id == NotificationStop.stopped_by)
                .where(
                    NotificationStop.entity_type == entity_type,
                    NotificationStop.entity_id == entity_id,
                    NotificationStop.end_date == end_date,
                    NotificationStop.resumed_at.is_(None),
                )
            )
        ).first()
        return StopInfo(row[1], row[0].stopped_at) if row else None

    async def stop(self, entity_type: str, entity_id: UUID, end_date: date, user: User) -> StopInfo:
        """Stop reminders for this document entry. Stopping twice keeps the first stop."""
        self._check(entity_type, user)
        inserted = await self.session.execute(
            insert(NotificationStop)
            .values(entity_type=entity_type, entity_id=entity_id, end_date=end_date, stopped_by=user.id)
            .on_conflict_do_nothing(
                index_elements=["entity_type", "entity_id", "end_date"],
                index_where=NotificationStop.resumed_at.is_(None),
            )
            .returning(NotificationStop.id)
        )
        if inserted.scalar() is not None:
            await self._log(user, "stop", entity_type, entity_id, end_date)
        info = await self.active(entity_type, entity_id, end_date)
        assert info is not None
        return info

    async def resume(self, entity_type: str, entity_id: UUID, end_date: date, user: User) -> bool:
        """Resume reminders; False when there was no active stop."""
        self._check(entity_type, user)
        result = await self.session.execute(
            update(NotificationStop)
            .where(
                NotificationStop.entity_type == entity_type,
                NotificationStop.entity_id == entity_id,
                NotificationStop.end_date == end_date,
                NotificationStop.resumed_at.is_(None),
            )
            .values(resumed_at=func.now(), resumed_by=user.id)
        )
        if not result.rowcount:
            return False
        await self._log(user, "resume", entity_type, entity_id, end_date)
        return True

    async def stopped_keys(self, pairs: list[tuple[str, UUID, date]]) -> dict[tuple[str, UUID], StopInfo]:
        """Active stops for many document entries at once, keyed by (entity_type, entity_id)."""
        if not pairs:
            return {}
        ids = {entity_id for _, entity_id, _ in pairs}
        rows = await self.session.execute(
            select(NotificationStop, User.full_name)
            .outerjoin(User, User.id == NotificationStop.stopped_by)
            .where(NotificationStop.entity_id.in_(ids), NotificationStop.resumed_at.is_(None))
        )
        wanted = set(pairs)
        return {
            (stop.entity_type, stop.entity_id): StopInfo(name, stop.stopped_at)
            for stop, name in rows.all()
            if (stop.entity_type, stop.entity_id, stop.end_date) in wanted
        }

    async def _log(self, user: User, action: str, entity_type: str, entity_id: UUID, end_date: date) -> None:
        await self.audit.log(
            user_id=user.id,
            action=AuditAction.UPDATE,
            entity_type="notification_stop",
            entity_id=entity_id,
            changes={"_label": entity_type, "action": action, "entity_type": entity_type, "end_date": str(end_date)},
        )
