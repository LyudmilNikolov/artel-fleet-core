from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.driver import Driver, DriverStatus
from app.repositories.base import BaseRepository
from app.repositories.driver_document_repo import drivers_with_status
from app.services.validity import local_today


class DriverRepository(BaseRepository[Driver]):
    def __init__(self, session: AsyncSession):
        super().__init__(Driver, session)

    async def search(
        self,
        *,
        q: str | None = None,
        status: DriverStatus | None = None,
        exclude_status: DriverStatus | None = None,
        department: str | None = None,
        doc_status: str | None = None,
        today: date | None = None,
        window: int = 30,
        user_id=None,
        offset: int = 0,
        limit: int = 50,
        order_by: str = "-created_at",
    ) -> tuple[list[Driver], int]:
        """Search drivers by name, phone or email; filter by status, department, document status or linked user.

        ``doc_status`` keeps drivers that have a current document in that computed status
        (``expired`` or ``expiring_soon``); it needs ``today``.
        """
        conditions = []
        if q:
            pattern = f"%{q}%"
            conditions.append(
                or_(Driver.full_name.ilike(pattern), Driver.phone.ilike(pattern), Driver.email.ilike(pattern))
            )
        if status:
            conditions.append(Driver.status == status)
        if exclude_status:
            conditions.append(Driver.status != exclude_status)
        if department:
            conditions.append(Driver.department == department)
        if user_id:
            conditions.append(Driver.user_id == user_id)
        if doc_status:
            conditions.append(Driver.id.in_(drivers_with_status(doc_status, today or local_today(), window)))

        total = (await self.session.execute(select(func.count()).select_from(Driver).where(*conditions))).scalar() or 0
        query = select(Driver).where(*conditions)

        desc = order_by.startswith("-")
        col_name = order_by.lstrip("-")
        if hasattr(Driver, col_name):
            col = getattr(Driver, col_name)
            query = query.order_by(col.desc() if desc else col.asc())

        result = await self.session.execute(query.offset(offset).limit(limit))
        return list(result.scalars().all()), total

    async def get_for_user(self, user_id) -> Driver | None:
        """The driver record linked to a system user, if any."""
        return await self.session.scalar(select(Driver).where(Driver.user_id == user_id).limit(1))
