"""Bell, inbox and item pages for documents that need attention, plus the stored messages tab."""

from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.i18n import _
from app.models.user import User, UserRole
from app.notification_rules import DRIVER_ENTITY, SOURCES, VEHICLE_ENTITY
from app.repositories.base import BaseRepository
from app.services.attention_service import (
    REMINDER_FILTERS,
    SORTS,
    STATUSES,
    AttentionItem,
    AttentionService,
    neighbours,
    sort_items,
)
from app.services.errors import DomainError
from app.services.notification_rule_service import NotificationRuleService, preview_text
from app.services.notification_service import NotificationService
from app.services.reminder_info_service import ReminderInfoService
from app.services.reminder_stop_service import MAY_STOP, ReminderStopService
from app.services.validity import local_today
from app.web.deps import driver_scope_of, get_web_user, web_require_roles

router = APIRouter(prefix="/notifications", tags=["web-notifications"])

BELL_ITEMS = 10
ENTITY_TYPES = (VEHICLE_ENTITY, DRIVER_ENTITY)


def _service(request: Request, db: AsyncSession) -> AttentionService:
    return AttentionService(db, scope=driver_scope_of(request))


def _render(request: Request, name: str, user: User | None, **values):
    return request.app.state.templates.TemplateResponse(
        request=request,
        name=name,
        context={
            "request": request,
            "user": user,
            "active_page": "notifications",
            **values,
            **request.app.state.template_globals(request),
        },
    )


def _clean(
    source: str | None, status: str | None, q: str | None, sort: str | None, reminders: str | None = None
) -> dict:
    """Validated filter values; anything unknown is dropped."""
    return {
        "source": source if source in {s.code for s in SOURCES} else None,
        "status": status if status in STATUSES else None,
        "q": (q or "").strip() or None,
        "sort": sort if sort in SORTS and sort != "urgency" else None,
        "reminders": reminders if reminders in REMINDER_FILTERS else None,
    }


@router.get("/bell", response_class=HTMLResponse)
async def notification_bell(request: Request, db: AsyncSession = Depends(get_db)):
    """The top-bar bell: number of documents needing attention (red when any is expired, amber otherwise)."""
    user = await get_web_user(request, db)
    if not user:
        return HTMLResponse("")
    items = await _service(request, db).items()
    return _render(
        request,
        "components/notification_bell.html",
        user,
        count=len(items),
        tone="expired" if any(i.status == "expired" for i in items) else "expiring",
    )


@router.get("/dropdown", response_class=HTMLResponse)
async def notification_dropdown(request: Request, db: AsyncSession = Depends(get_db)):
    """Bell dropdown: the most urgent items grouped Expired / Expiring soon."""
    user = await get_web_user(request, db)
    if not user:
        return HTMLResponse("")
    items = await _service(request, db).items()
    top = items[:BELL_ITEMS]
    return _render(
        request,
        "components/notification_dropdown.html",
        user,
        expired=[i for i in top if i.status == "expired"],
        expiring=[i for i in top if i.status == "expiring_soon"],
        total=len(items),
        unread_messages=await NotificationService(db).get_unread_count(user.id),
    )


@router.post("/{notification_id}/read", response_class=HTMLResponse)
async def mark_read(notification_id: UUID, request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return HTMLResponse("")
    await NotificationService(db).mark_as_read(notification_id, user.id)
    return HTMLResponse("")


@router.post("/mark-all-read", response_class=HTMLResponse)
async def mark_all_read(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return HTMLResponse("")
    await NotificationService(db).mark_all_as_read(user.id)
    return HTMLResponse(_("notifications.all_read", request.state.locale))


@router.get("", response_class=HTMLResponse)
async def notifications_page(
    request: Request,
    tab: str = "documents",
    source: str | None = None,
    status: str | None = None,
    q: str | None = None,
    sort: str | None = None,
    reminders: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Inbox. Documents tab: every current item needing attention; Messages tab: stored messages."""
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    unread = await NotificationService(db).get_unread_count(user.id)
    if tab == "messages":
        messages = await NotificationService(db).get_notifications(user.id, limit=50, include_read=True)
        return _render(request, "notifications/inbox.html", user, tab="messages", messages=messages, unread=unread)

    filters = _clean(source, status, q, sort, reminders)
    items = sort_items(
        await _service(request, db).items(filters["source"], filters["status"], filters["q"], filters["reminders"]),
        sort,
    )
    total = len(items)
    start = (page - 1) * size
    partial = request.headers.get("HX-Request") == "true" and request.headers.get("HX-Target") == "attention-table"
    return _render(
        request,
        "notifications/partials/table.html" if partial else "notifications/inbox.html",
        user,
        tab="documents",
        items=items[start : start + size],
        total=total,
        page=page,
        size=size,
        pages=BaseRepository.calc_pages(total, size),
        filters=filters,
        sources=SOURCES,
        statuses=STATUSES,
        reminder_filters=REMINDER_FILTERS,
        sorts=SORTS,
        unread=unread,
        filter_query=urlencode({k: v for k, v in filters.items() if v}),
    )


@router.get("/item/{entity_type}/{entity_id}", response_class=HTMLResponse)
async def notification_item(
    request: Request,
    entity_type: str,
    entity_id: UUID,
    source: str | None = None,
    status: str | None = None,
    q: str | None = None,
    sort: str | None = None,
    reminders: str | None = None,
    done: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """One document needing attention, with Previous / Next across the inbox list it was opened from."""
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    service = _service(request, db)
    item = await service.find(entity_type, entity_id) if entity_type in ENTITY_TYPES else None
    if item is None:  # renewed, no longer due, or outside the user's scope (for example an old link)
        return RedirectResponse(url="/notifications", status_code=302)

    filters = _clean(source, status, q, sort, reminders)
    listed = sort_items(
        await service.items(filters["source"], filters["status"], filters["q"], filters["reminders"]), sort
    )
    previous, following = neighbours(listed, item.key)
    position = next((n for n, i in enumerate(listed, start=1) if i.key == item.key), None)
    carried = urlencode({k: v for k, v in filters.items() if v})

    manages = user.role in MAY_STOP
    stop_info = (
        await ReminderStopService(db).active(item.entity_type, item.entity_id, item.end_date) if item.stopped else None
    )
    rule = next_reminder = history = rule_preview = None
    if manages:
        loaded = await NotificationRuleService(db).rule(item.source)
        info = ReminderInfoService(db)
        next_reminder = await info.next_reminder(item, loaded, local_today())
        if user.role == UserRole.ADMIN:  # the rule, its recipients and the send history are administrator-only
            rule = loaded
            history = await info.history(item)
            t = request.app.state.template_globals(request)["_"]
            rule_preview = preview_text(t, rule.milestone_days, rule.repeat_every_days, rule.stop_after_overdue_days)
    return _render(
        request,
        "notifications/item.html",
        user,
        item=item,
        previous=previous,
        previous_url=_with_filters(previous, carried) if previous else None,
        following=following,
        following_url=_with_filters(following, carried) if following else None,
        position=position,
        total=len(listed),
        carried=carried,
        can_stop=manages,
        can_edit_rules=user.role == UserRole.ADMIN,
        stop_info=stop_info,
        rule=rule,
        rule_preview=rule_preview,
        next_reminder=next_reminder,
        history=history,
        done=done if done in ("stopped", "resumed") else None,
    )


async def _change_stop(request: Request, db: AsyncSession, user, entity_type: str, entity_id: UUID, stop: bool):
    query = {k: v for k, v in request.query_params.items() if k in ("source", "status", "q", "sort", "reminders")}
    item = await _service(request, db).find(entity_type, entity_id) if entity_type in ENTITY_TYPES else None
    if item is None:
        return RedirectResponse(url="/notifications", status_code=303)
    svc = ReminderStopService(db)
    try:
        if stop:
            await svc.stop(item.entity_type, item.entity_id, item.end_date, user)
        else:
            await svc.resume(item.entity_type, item.entity_id, item.end_date, user)
    except DomainError:
        return RedirectResponse(url="/notifications", status_code=303)
    query["done"] = "stopped" if stop else "resumed"
    return RedirectResponse(url=f"{item.url}?{urlencode(query)}", status_code=303)


require_manager = web_require_roles(UserRole.ADMIN, UserRole.FLEET_MANAGER)


@router.post("/item/{entity_type}/{entity_id}/stop")
async def stop_reminders(
    request: Request,
    entity_type: str,
    entity_id: UUID,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_manager),
):
    """Stop reminders for one document, for everyone and every channel (admin and fleet manager)."""
    return await _change_stop(request, db, user, entity_type, entity_id, stop=True)


@router.post("/item/{entity_type}/{entity_id}/resume")
async def resume_reminders(
    request: Request,
    entity_type: str,
    entity_id: UUID,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_manager),
):
    return await _change_stop(request, db, user, entity_type, entity_id, stop=False)


def _with_filters(item: AttentionItem, carried: str) -> str:
    """Item page link that keeps the inbox filters, so Previous / Next stay inside the same list."""
    return f"{item.url}?{carried}" if carried else item.url
