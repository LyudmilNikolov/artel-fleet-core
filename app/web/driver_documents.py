"""Web UI for per-driver tracked documents (Лицензи и документи)."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.driver_document_rules import DRIVER_DOCUMENT_TYPES, DRIVING_LICENCE_CATEGORIES, mask_number
from app.models.driver import Driver
from app.models.user import User, UserRole
from app.schemas.driver_document import DriverDocumentCreate, DriverDocumentRenew, DriverDocumentUpdate
from app.services.driver_access import (
    can_edit_driver_documents,
    can_read_driver_documents,
    can_see_numbers,
    can_view_scans_of,
    is_manager,
)
from app.services.driver_document_service import DriverDocumentService
from app.services.errors import DomainError, render_error
from app.web.deps import get_web_user, web_require_roles

router = APIRouter(prefix="/driver-documents", tags=["web-driver-documents"])
require_manager = web_require_roles(UserRole.ADMIN, UserRole.FLEET_MANAGER)
FORM_FIELDS = ("document_number", "categories", "start_date", "end_date", "notes")
FIELD_FOR_ERROR = {
    "error.driver_document_number_required": "document_number",
    "error.driver_document_categories_not_allowed": "categories",
    "error.driver_document_category_invalid": "categories",
    "error.driver_document_end_before_start": "end_date",
    "error.driver_document_type_unknown": "document_type",
}


def _ctx(request: Request, user: User, **values) -> dict:
    return {
        "request": request,
        "user": user,
        "active_page": "drivers",
        "can_edit": user.role in (UserRole.ADMIN, UserRole.FLEET_MANAGER),
        **values,
        **request.app.state.template_globals(request),
    }


def _type_specs(request: Request) -> list[dict]:
    """Type options for the form, in display order, straight from the central definition."""
    t = request.app.state.template_globals(request)["_"]
    return [
        {
            "code": code,
            "label": t("driver_document_type." + code),
            "number_required": spec.number_required,
            "has_categories": spec.has_categories,
        }
        for code, spec in DRIVER_DOCUMENT_TYPES.items()
    ]


def _errors(exc: Exception, lang: str) -> tuple[str, dict]:
    """Return (summary message, per-field messages) for a validation or domain error."""
    field_errors: dict[str, str] = {}
    if isinstance(exc, ValidationError):
        for detail in exc.errors():
            key = detail.get("ctx", {}).get("error")
            name = str(detail["loc"][0]) if detail["loc"] else "end_date"
            field_errors[name] = render_error(str(key) if key else "errors.invalid_input", lang)
        first = next(iter(field_errors.values()), None)
        return first or render_error(exc, lang), field_errors
    if isinstance(exc, DomainError) and exc.key in FIELD_FOR_ERROR:
        field_errors[FIELD_FOR_ERROR[exc.key]] = render_error(exc, lang)
    return render_error(exc, lang), field_errors


def _submitted(form) -> dict:
    """Form fields with blanks as None; the category checkboxes are joined into one value."""
    values = {k: (v.strip() or None) if isinstance(v, str) else v for k, v in form.items() if isinstance(v, str)}
    values["categories"] = ", ".join(form.getlist("categories")) or None
    return values


async def _render_form(
    request: Request,
    user: User,
    driver: Driver,
    *,
    mode: str,
    form_data: dict,
    source=None,
    error: str | None = None,
    field_errors: dict | None = None,
):
    action = "/driver-documents/new" if mode == "create" else f"/driver-documents/{source.id}/{mode}"
    selected = str(form_data.get("document_type") or "")
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="driver_documents/form.html",
        status_code=422 if error else 200,
        context=_ctx(
            request,
            user,
            mode=mode,
            action=action,
            driver=driver,
            source=source,
            form_data=form_data,
            types=_type_specs(request),
            category_options=DRIVING_LICENCE_CATEGORIES,
            selected_categories=[c.strip() for c in str(form_data.get("categories") or "").split(",") if c.strip()],
            selected_type=selected,
            error=error,
            field_errors=field_errors or {},
        ),
    )


async def _driver(db: AsyncSession, driver_id: str | None) -> Driver | None:
    try:
        return await db.get(Driver, UUID(str(driver_id))) if driver_id else None
    except ValueError:
        return None


@router.get("/new", response_class=HTMLResponse)
async def driver_document_new(
    request: Request,
    driver_id: str | None = None,
    document_type: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    driver = await _driver(db, driver_id)
    if not driver:
        return RedirectResponse(url="/drivers", status_code=302)
    if not can_edit_driver_documents(user, driver):
        return RedirectResponse(url="/", status_code=302)
    return await _render_form(request, user, driver, mode="create", form_data={"document_type": document_type})


@router.post("/new", response_class=HTMLResponse)
async def driver_document_create(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    raw = _submitted(await request.form())
    driver = await _driver(db, raw.get("driver_id"))
    if not driver:
        return RedirectResponse(url="/drivers", status_code=302)
    if not can_edit_driver_documents(user, driver):
        return RedirectResponse(url="/", status_code=302)
    try:
        view = await DriverDocumentService(db).create(DriverDocumentCreate(**raw), user.id)
    except (ValidationError, DomainError) as exc:
        error, field_errors = _errors(exc, request.state.locale)
        return await _render_form(
            request, user, driver, mode="create", form_data=raw, error=error, field_errors=field_errors
        )
    return RedirectResponse(url=f"/driver-documents/{view.id}", status_code=302)


@router.get("/{entry_id}", response_class=HTMLResponse)
async def driver_document_detail(request: Request, entry_id: UUID, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    service = DriverDocumentService(db)
    view = await service.get(entry_id)
    if not view or not can_read_driver_documents(user, view.driver):
        return RedirectResponse(url="/drivers", status_code=302)
    spec = DRIVER_DOCUMENT_TYPES.get(view.document_type)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="driver_documents/detail.html",
        context=_ctx(
            request,
            user,
            entry=view,
            can_edit=can_edit_driver_documents(user, view.driver),
            can_delete=is_manager(user),
            shown_number=view.document_number
            if can_see_numbers(user, view.driver)
            else mask_number(view.document_number),
            can_view_scans=can_view_scans_of(user, view.driver),
            successor=await service.successor(entry_id),
            history_count=await service.history_count(entry_id),
            upload_doc_type=spec.upload_doc_type if spec else "scan",
        ),
    )


@router.get("/{entry_id}/edit", response_class=HTMLResponse)
async def driver_document_edit(request: Request, entry_id: UUID, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    source = await DriverDocumentService(db).get(entry_id)
    if not source:
        return RedirectResponse(url="/drivers", status_code=302)
    if not can_edit_driver_documents(user, source.driver):
        return RedirectResponse(url="/", status_code=302)
    form_data = {name: getattr(source, name) for name in FORM_FIELDS}
    form_data["document_type"] = source.document_type
    return await _render_form(request, user, source.driver, mode="edit", form_data=form_data, source=source)


@router.post("/{entry_id}/edit", response_class=HTMLResponse)
async def driver_document_update(request: Request, entry_id: UUID, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    service = DriverDocumentService(db)
    source = await service.get(entry_id)
    if not source:
        return RedirectResponse(url="/drivers", status_code=302)
    if not can_edit_driver_documents(user, source.driver):
        return RedirectResponse(url="/", status_code=302)
    raw = _submitted(await request.form())
    values = {name: raw.get(name) for name in FORM_FIELDS}
    try:
        await service.update(entry_id, DriverDocumentUpdate(**values), user.id)
    except (ValidationError, DomainError) as exc:
        error, field_errors = _errors(exc, request.state.locale)
        raw["document_type"] = source.document_type
        return await _render_form(
            request,
            user,
            source.driver,
            mode="edit",
            form_data=raw,
            source=source,
            error=error,
            field_errors=field_errors,
        )
    return RedirectResponse(url=f"/driver-documents/{entry_id}", status_code=302)


@router.get("/{entry_id}/renew", response_class=HTMLResponse)
async def driver_document_renew_form(request: Request, entry_id: UUID, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    service = DriverDocumentService(db)
    source = await service.get(entry_id)
    if not source:
        return RedirectResponse(url="/drivers", status_code=302)
    if not can_edit_driver_documents(user, source.driver):
        return RedirectResponse(url="/", status_code=302)
    form_data = {**service.renewal_proposal(source), "document_type": source.document_type}
    return await _render_form(request, user, source.driver, mode="renew", form_data=form_data, source=source)


@router.post("/{entry_id}/renew", response_class=HTMLResponse)
async def driver_document_renew(request: Request, entry_id: UUID, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    service = DriverDocumentService(db)
    source = await service.get(entry_id)
    if not source:
        return RedirectResponse(url="/drivers", status_code=302)
    if not can_edit_driver_documents(user, source.driver):
        return RedirectResponse(url="/", status_code=302)
    raw = _submitted(await request.form())
    try:
        # The form always sends every field, so number and categories are taken as submitted (blank clears).
        view = await service.renew(entry_id, DriverDocumentRenew(**{n: raw.get(n) for n in FORM_FIELDS}), user.id)
    except (ValidationError, DomainError) as exc:
        error, field_errors = _errors(exc, request.state.locale)
        raw["document_type"] = source.document_type
        return await _render_form(
            request,
            user,
            source.driver,
            mode="renew",
            form_data=raw,
            source=source,
            error=error,
            field_errors=field_errors,
        )
    return RedirectResponse(url=f"/driver-documents/{view.id}", status_code=302)


@router.post("/{entry_id}/delete")
async def driver_document_delete(
    request: Request, entry_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)
):
    service = DriverDocumentService(db)
    source = await service.get(entry_id)
    target = f"/drivers/{source.driver_id}#licences" if source else "/drivers"
    scope = (await request.form()).get("scope", "entry")
    try:
        await service.delete(entry_id, scope, user.id)
    except DomainError:
        pass
    return RedirectResponse(url=target, status_code=303)
