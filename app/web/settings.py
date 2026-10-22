"""Web routes for admin settings: users and audit log."""

import datetime as dt
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.audit_log import AuditAction
from app.models.user import User, UserRole
from app.repositories.user_repo import UserRepository
from app.services.audit_service import AuditService
from app.services.errors import DomainError, render_error
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


AUDIT_ENTITY_TYPES = (
    "vehicle",
    "driver",
    "driver_document",
    "maintenance",
    "expense",
    "compliance_document",
    "mileage_log",
    "document",
    "user",
    "session",
    "report",
)


def _parse(kind, value: str | None, error_key: str):
    """Convert an optional query value; a bad value is a DomainError, not a 500."""
    if not value:
        return None
    try:
        return kind(value)
    except ValueError:
        raise DomainError(error_key) from None


def _date(value: str | None) -> dt.date | None:
    return _parse(dt.date.fromisoformat, value, "error.report_invalid_date")


@router.get("/settings/audit")
async def audit_log_page(
    request: Request,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    action: str | None = Query(None),
    entity_type: str | None = Query(None),
    user_id: str | None = Query(None),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Searchable audit log: filter by action, entity, user and an inclusive UTC date range."""
    templates = request.app.state.templates
    ctx = request.app.state.template_globals(request)

    logs, total, error = [], 0, None
    try:
        if _date(start_date) and _date(end_date) and _date(end_date) < _date(start_date):
            raise DomainError("error.report_date_order")
        logs, total = await AuditService(db).list_logs(
            user_id=_parse(UUID, user_id, "errors.invalid_input"),
            action=_parse(AuditAction, action, "errors.invalid_input"),
            entity_type=entity_type or None,
            start_date=_date(start_date),
            end_date=_date(end_date),
            page=page,
            size=size,
        )
    except DomainError as exc:
        error = render_error(exc, request.state.locale)
    users = (await UserRepository(db).list(offset=0, limit=500, order_by="full_name"))[0]

    return templates.TemplateResponse(
        request=request,
        name="settings/audit_log.html",
        status_code=422 if error else 200,
        context={
            "request": request,
            "user": user,
            "logs": logs,
            "total": total,
            "page": page,
            "size": size,
            "pages": max(1, (total + size - 1) // size),
            "action_filter": action or "",
            "entity_type_filter": entity_type or "",
            "user_filter": user_id or "",
            "start_date": start_date or "",
            "end_date": end_date or "",
            "actions": [a.value for a in AuditAction],
            "entity_types": AUDIT_ENTITY_TYPES,
            "users": users,
            "filter_error": error,
            "active_page": "audit",
            **ctx,
        },
    )
