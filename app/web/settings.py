"""Web routes for admin settings: users and audit log."""

import datetime as dt
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.audit_log import AuditAction
from app.models.driver import Driver
from app.models.user import User, UserRole
from app.repositories.driver_repo import DriverRepository
from app.repositories.user_repo import UserRepository
from app.services.audit_service import AuditService
from app.services.errors import DomainError, render_error
from app.services.user_service import UserService, valid_email
from app.utils.password_policy import validate_password
from app.utils.security import hash_password
from app.web.deps import web_require_roles

router = APIRouter()

require_admin = web_require_roles(UserRole.ADMIN)


USER_LANGUAGES = ("bg", "en")


async def _user_form_response(
    request: Request, admin: User, db: AsyncSession, *, target=None, form=None, error=None, status_code=200
):
    drivers = await UserService(db).linkable_drivers(target.id if target else None)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="settings/user_form.html",
        status_code=status_code,
        context={
            "request": request,
            "user": admin,
            "target": target,
            "form_data": form or {},
            "error": error,
            "roles": [r.value for r in UserRole],
            "driver_profiles": drivers,
            "profiles_map": {str(d.id): {"full_name": d.full_name, "email": d.email or ""} for d in drivers},
            "language_choices": USER_LANGUAGES,  # not "languages": that name is a template global
            "is_self": bool(target and target.id == admin.id),
            "active_page": "users",
            **request.app.state.template_globals(request),
        },
    )


async def _fill_from_profile(db: AsyncSession, data: dict, target: User | None = None) -> None:
    """A driver login takes its name and email from the driver profile (the form fields are locked)."""
    profile = await UserService(db).profile_for_form(data["role"], data["driver_id"], target)
    if profile is not None:
        data["full_name"] = profile.full_name
        if profile.email:
            data["email"] = profile.email


async def _read_user_form(request: Request) -> tuple[dict, str, str]:
    raw = await request.form()
    data = {
        k: str(raw.get(k) or "").strip() for k in ("username", "email", "full_name", "role", "language", "driver_id")
    }
    data["is_active"] = "on" if raw.get("is_active") else ""
    return data, str(raw.get("password") or ""), str(raw.get("password_confirm") or "")


def _validate_user_form(data: dict, password: str, confirm: str, *, creating: bool) -> tuple[UserRole, UUID | None]:
    """Raise DomainError with a specific key for the first problem; returns the role and driver profile id.

    The driver profile only applies to the driver role; for other roles a submitted value is ignored.
    """
    if (creating or data["username"]) and not 3 <= len(data["username"]) <= 100:
        raise DomainError("error.user_username_invalid")
    if not data["full_name"]:
        raise DomainError("error.user_name_required")
    if not valid_email(data["email"]):
        raise DomainError("error.user_email_invalid")
    if data["language"] not in USER_LANGUAGES:
        raise DomainError("errors.invalid_input")
    try:
        role = UserRole(data["role"])
    except ValueError:
        raise DomainError("errors.invalid_input") from None
    if creating or password:
        validate_password(password, data["username"] or None)
        if password != confirm:
            raise DomainError("error.user_password_mismatch")
    driver_id = None
    if role == UserRole.DRIVER and data["driver_id"]:
        try:
            driver_id = UUID(data["driver_id"])
        except ValueError:
            raise DomainError("error.driver_profile_not_found") from None
    return role, driver_id


@router.get("/settings/users/new")
async def user_new_page(request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    return await _user_form_response(request, user, db, form={"role": UserRole.VIEWER.value, "language": "bg"})


@router.post("/settings/users/new")
async def user_create(request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    data, password, confirm = await _read_user_form(request)
    await _fill_from_profile(db, data)
    try:
        role, driver_id = _validate_user_form(data, password, confirm, creating=True)
        await UserService(db).create(
            email=data["email"],
            username=data["username"],
            full_name=data["full_name"],
            password=password,
            role=role,
            language=data["language"],
            driver_id=driver_id,
        )
    except DomainError as exc:
        return await _user_form_response(
            request, user, db, form=data, error=render_error(exc, request.state.locale), status_code=422
        )
    return RedirectResponse(url="/settings/users", status_code=303)


@router.get("/settings/users/{user_id}/edit")
async def user_edit_page(
    request: Request, user_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)
):
    target = await UserRepository(db).get_by_id(user_id)
    if not target:
        return RedirectResponse(url="/settings/users", status_code=303)
    form = {
        "email": target.email,
        "full_name": target.full_name,
        "role": target.role.value,
        "language": target.language,
        "is_active": "on" if target.is_active else "",
        "driver_id": str(linked.id) if (linked := await DriverRepository(db).get_for_user(target.id)) else "",
        "username": target.username,
    }
    if linked:  # the profile is the source of a driver login's name and email
        form["full_name"] = linked.full_name
        if linked.email:
            form["email"] = linked.email
    return await _user_form_response(request, user, db, target=target, form=form)


@router.post("/settings/users/{user_id}/edit")
async def user_update(
    request: Request, user_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)
):
    repo = UserRepository(db)
    target = await repo.get_by_id(user_id)
    if not target:
        return RedirectResponse(url="/settings/users", status_code=303)
    data, password, confirm = await _read_user_form(request)
    await _fill_from_profile(db, data, target)
    try:
        role, driver_id = _validate_user_form(data, password, confirm, creating=False)
        is_active = bool(data["is_active"])
        if target.id == user.id and (role != target.role or not is_active):
            raise DomainError("error.user_self_locked")  # an admin cannot lock themselves out
        changes = {
            "full_name": data["full_name"],
            "email": data["email"],
            "role": role,
            "language": data["language"],
            "is_active": is_active,
        }
        if data["username"]:
            changes["username"] = data["username"]
        if password:
            changes["hashed_password"] = hash_password(password)
        await UserService(db).update(target, changes, driver_id=driver_id)  # also keeps username / email unique
    except DomainError as exc:
        return await _user_form_response(
            request, user, db, target=target, form=data, error=render_error(exc, request.state.locale), status_code=422
        )
    return RedirectResponse(url="/settings/users", status_code=303)


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
    linked = await db.scalars(select(Driver).where(Driver.user_id.in_([u.id for u in items])))
    driver_profiles = {d.user_id: d for d in linked.all()}

    return templates.TemplateResponse(
        request=request,
        name="settings/users.html",
        context={
            "request": request,
            "user": user,
            "driver_profiles": driver_profiles,
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
    "driver_document_change",
    "maintenance",
    "expense",
    "compliance_document",
    "mileage_log",
    "document",
    "user",
    "session",
    "report",
    "notification_rule",
    "notification_settings",
    "notification_stop",
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
