"""Web routes for admin settings: users and audit log."""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.user import User, UserRole
from app.repositories.user_repo import UserRepository
from app.web.deps import web_require_roles

router = APIRouter()

require_admin = web_require_roles(UserRole.ADMIN)


@router.get("/settings/users")
async def users_page(
    request: Request,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    templates = request.app.state.templates
    ctx = request.app.state.template_globals(request)

    repo = UserRepository(db)
    items, total = await repo.list(offset=(page - 1) * size, limit=size, order_by="-created_at")
    pages = max(1, (total + size - 1) // size)

    return templates.TemplateResponse(
        request=request,
        name="settings/users.html",
        context={
            "request": request,
            "user": user,
            "users": items,
            "total": total,
            "page": page,
            "size": size,
            "pages": pages,
            "active_page": "users",
            **ctx,
        },
    )


@router.get("/settings/audit")
async def audit_log_page(
    request: Request,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    action: str | None = Query(None),
    entity_type: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    templates = request.app.state.templates
    ctx = request.app.state.template_globals(request)

    query = select(AuditLog).options(selectinload(AuditLog.user)).order_by(AuditLog.timestamp.desc())
    count_query = select(func.count()).select_from(AuditLog)

    if action:
        query = query.where(AuditLog.action == action)
        count_query = count_query.where(AuditLog.action == action)
    if entity_type:
        query = query.where(AuditLog.entity_type == entity_type)
        count_query = count_query.where(AuditLog.entity_type == entity_type)

    total = (await db.execute(count_query)).scalar() or 0
    pages = max(1, (total + size - 1) // size)

    result = await db.execute(query.offset((page - 1) * size).limit(size))
    logs = result.scalars().all()

    return templates.TemplateResponse(
        request=request,
        name="settings/audit_log.html",
        context={
            "request": request,
            "user": user,
            "logs": logs,
            "total": total,
            "page": page,
            "size": size,
            "pages": pages,
            "action_filter": action,
            "entity_type_filter": entity_type,
            "active_page": "audit",
            **ctx,
        },
    )
