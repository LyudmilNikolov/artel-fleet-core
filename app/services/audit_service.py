import datetime as dt
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.audit_context import current_audit_context
from app.models.audit_log import AuditAction, AuditLog


class AuditService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def log(
        self,
        *,
        user_id: UUID | None,
        action: AuditAction,
        entity_type: str,
        entity_id: UUID | None = None,
        changes: dict | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuditLog:
        context = current_audit_context()
        entry = AuditLog(
            user_id=user_id or context.get("user_id"),
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            changes=changes,
            ip_address=ip_address or context.get("ip_address"),
            user_agent=user_agent or context.get("user_agent"),
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def log_export(self, *, user_id: UUID | None, report: str, file_format: str, filters: dict | None = None):
        """Record that a report was exported, with the filters it was exported with."""
        return await self.log(
            user_id=user_id,
            action=AuditAction.EXPORT,
            entity_type="report",
            changes={"report": report, "format": file_format, **(filters or {})},
        )

    async def list_logs(
        self,
        *,
        user_id: UUID | None = None,
        action: AuditAction | None = None,
        entity_type: str | None = None,
        start_date: dt.date | None = None,
        end_date: dt.date | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[AuditLog], int]:
        """Audit entries, newest first. Dates are inclusive and compared in UTC (timestamps are stored in UTC)."""
        conditions = []
        if user_id:
            conditions.append(AuditLog.user_id == user_id)
        if action:
            conditions.append(AuditLog.action == action)
        if entity_type:
            conditions.append(AuditLog.entity_type == entity_type)
        if start_date:
            conditions.append(AuditLog.timestamp >= dt.datetime.combine(start_date, dt.time.min, dt.UTC))
        if end_date:
            conditions.append(
                AuditLog.timestamp < dt.datetime.combine(end_date + dt.timedelta(days=1), dt.time.min, dt.UTC)
            )

        total = (
            await self.session.execute(select(func.count()).select_from(AuditLog).where(*conditions))
        ).scalar() or 0
        result = await self.session.execute(
            select(AuditLog)
            .options(selectinload(AuditLog.user))
            .where(*conditions)
            .order_by(AuditLog.timestamp.desc(), AuditLog.id)
            .offset((page - 1) * size)
            .limit(size)
        )
        return list(result.scalars().all()), total

    @staticmethod
    def compute_diff(old: dict, new: dict) -> dict:
        changes = {}
        for key in set(list(old.keys()) + list(new.keys())):
            old_val = old.get(key)
            new_val = new.get(key)
            if old_val != new_val:
                changes[key] = {"old": old_val, "new": new_val}
        return changes
