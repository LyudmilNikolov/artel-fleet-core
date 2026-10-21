from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.i18n import _
from app.services.notification_service import NotificationService
from app.web.deps import get_web_user

router = APIRouter(prefix="/notifications", tags=["web-notifications"])


@router.get("/bell", response_class=HTMLResponse)
async def notification_bell(request: Request, db: AsyncSession = Depends(get_db)):
    """Return notification bell with unread count (polled via HTMX)."""
    user = await get_web_user(request, db)
    if not user:
        return HTMLResponse("")
    svc = NotificationService(db)
    count = await svc.get_unread_count(user.id)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="components/notification_bell.html",
        context={
            "request": request,
            "unread_count": count,
            **request.app.state.template_globals(request),
        },
    )


@router.get("/dropdown", response_class=HTMLResponse)
async def notification_dropdown(request: Request, db: AsyncSession = Depends(get_db)):
    """Return notification dropdown content."""
    user = await get_web_user(request, db)
    if not user:
        return HTMLResponse("")
    svc = NotificationService(db)
    notifications = await svc.get_notifications(user.id, limit=10)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="components/notification_dropdown.html",
        context={
            "request": request,
            "notifications": notifications,
            **request.app.state.template_globals(request),
        },
    )


@router.post("/{notification_id}/read", response_class=HTMLResponse)
async def mark_read(
    notification_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_web_user(request, db)
    if not user:
        return HTMLResponse("")
    svc = NotificationService(db)
    await svc.mark_as_read(notification_id, user.id)
    return HTMLResponse("")


@router.post("/mark-all-read", response_class=HTMLResponse)
async def mark_all_read(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return HTMLResponse("")
    svc = NotificationService(db)
    await svc.mark_all_as_read(user.id)
    return HTMLResponse(_("notifications.all_read", request.state.locale))


@router.get("", response_class=HTMLResponse)
async def notifications_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    svc = NotificationService(db)
    notifications = await svc.get_notifications(user.id, limit=50, include_read=True)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="settings/notifications.html",
        context={
            "request": request,
            "user": user,
            "active_page": "notifications",
            "notifications": notifications,
            **request.app.state.template_globals(request),
        },
    )
