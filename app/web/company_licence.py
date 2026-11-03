"""Web UI for the company licence (international haulage licence): view, add, edit, renew, delete."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User, UserRole
from app.schemas.company_licence import CompanyLicenceInput
from app.services.company_licence_service import CompanyLicenceService
from app.services.errors import DomainError, render_error
from app.web.deps import get_web_user, web_require_roles
from app.web.form_errors import banner_for, field_errors_from

router = APIRouter(prefix="/company-licence", tags=["web-company-licence"])
require_manager = web_require_roles(UserRole.ADMIN, UserRole.FLEET_MANAGER)
FORM_FIELDS = ("licence_number", "holder", "start_date", "end_date", "issued_by", "notes")
INDEX = "/company-licence"


def _ctx(request: Request, user: User, **values) -> dict:
    return {
        "request": request,
        "user": user,
        "active_page": "company_licence",
        "can_edit": user.role in (UserRole.ADMIN, UserRole.FLEET_MANAGER),
        **values,
        **request.app.state.template_globals(request),
    }


def _submitted(form) -> dict:
    return {name: (form.get(name) or None) for name in FORM_FIELDS}


def _errors(exc: Exception, lang: str) -> tuple[str, dict]:
    if isinstance(exc, ValidationError):
        field_errors = field_errors_from(exc, lang)
        if "_form" in field_errors:  # the end-before-start rule belongs to the end date
            field_errors["end_date"] = field_errors.pop("_form")
        return banner_for(exc, field_errors, lang), field_errors
    return render_error(exc, lang), {}


def _form_response(request, user, *, mode, action, form_data, source=None, error=None, field_errors=None):
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="company_licence/form.html",
        status_code=422 if error else 200,
        context=_ctx(
            request,
            user,
            mode=mode,
            action=action,
            source=source,
            form_data=form_data,
            error=error,
            field_errors=field_errors or {},
        ),
    )


@router.get("", response_class=HTMLResponse)
async def company_licence_index(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role == UserRole.DRIVER:  # the company licence is not part of a driver's own area
        return RedirectResponse(url="/", status_code=302)
    entries = await CompanyLicenceService(db).list_entries()
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="company_licence/index.html",
        context=_ctx(request, user, entries=entries, current=entries[0] if entries else None, history=entries[1:]),
    )


@router.get("/new", response_class=HTMLResponse)
async def company_licence_new(request: Request, user: User = Depends(require_manager)):
    return _form_response(request, user, mode="create", action=f"{INDEX}/new", form_data={})


@router.post("/new", response_class=HTMLResponse)
async def company_licence_create(
    request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)
):
    raw = _submitted(await request.form())
    try:
        await CompanyLicenceService(db).create(CompanyLicenceInput(**raw), user.id)
    except (ValidationError, DomainError) as exc:
        error, field_errors = _errors(exc, request.state.locale)
        return _form_response(
            request, user, mode="create", action=f"{INDEX}/new", form_data=raw, error=error, field_errors=field_errors
        )
    return RedirectResponse(url=INDEX, status_code=302)


@router.get("/{entry_id}/edit", response_class=HTMLResponse)
async def company_licence_edit(
    request: Request, entry_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)
):
    source = await CompanyLicenceService(db).get(entry_id)
    if not source:
        return RedirectResponse(url=INDEX, status_code=302)
    form_data = {name: getattr(source, name) for name in FORM_FIELDS}
    return _form_response(
        request, user, mode="edit", action=f"{INDEX}/{entry_id}/edit", form_data=form_data, source=source
    )


@router.post("/{entry_id}/edit", response_class=HTMLResponse)
async def company_licence_update(
    request: Request, entry_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)
):
    service = CompanyLicenceService(db)
    source = await service.get(entry_id)
    if not source:
        return RedirectResponse(url=INDEX, status_code=302)
    raw = _submitted(await request.form())
    try:
        await service.update(entry_id, CompanyLicenceInput(**raw))
    except (ValidationError, DomainError) as exc:
        error, field_errors = _errors(exc, request.state.locale)
        return _form_response(
            request,
            user,
            mode="edit",
            action=f"{INDEX}/{entry_id}/edit",
            form_data=raw,
            source=source,
            error=error,
            field_errors=field_errors,
        )
    return RedirectResponse(url=INDEX, status_code=302)


@router.get("/{entry_id}/renew", response_class=HTMLResponse)
async def company_licence_renew_form(
    request: Request, entry_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)
):
    service = CompanyLicenceService(db)
    source = await service.get(entry_id)
    if not source:
        return RedirectResponse(url=INDEX, status_code=302)
    return _form_response(
        request,
        user,
        mode="renew",
        action=f"{INDEX}/{entry_id}/renew",
        form_data=service.renewal_proposal(source),
        source=source,
    )


@router.post("/{entry_id}/renew", response_class=HTMLResponse)
async def company_licence_renew(
    request: Request, entry_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)
):
    service = CompanyLicenceService(db)
    source = await service.get(entry_id)
    if not source:
        return RedirectResponse(url=INDEX, status_code=302)
    raw = _submitted(await request.form())
    try:
        await service.create(CompanyLicenceInput(**raw), user.id, renewed_from_id=entry_id)
    except (ValidationError, DomainError) as exc:
        error, field_errors = _errors(exc, request.state.locale)
        return _form_response(
            request,
            user,
            mode="renew",
            action=f"{INDEX}/{entry_id}/renew",
            form_data=raw,
            source=source,
            error=error,
            field_errors=field_errors,
        )
    return RedirectResponse(url=INDEX, status_code=302)


@router.post("/{entry_id}/delete")
async def company_licence_delete(
    entry_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)
):
    try:
        await CompanyLicenceService(db).delete(entry_id)
    except DomainError:
        pass
    return RedirectResponse(url=INDEX, status_code=303)
