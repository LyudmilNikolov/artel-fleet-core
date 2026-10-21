"""Web UI for per-vehicle compliance documents (Документи и срокове)."""

from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User, UserRole
from app.models.vehicle import Vehicle
from app.repositories.base import BaseRepository
from app.repositories.compliance_repo import STATUSES
from app.schemas.compliance import ComplianceCreate, ComplianceRenew, ComplianceUpdate
from app.services.compliance_service import ComplianceService
from app.services.errors import DomainError, render_error
from app.vehicle_rules import ComplianceType, compliance_types_for
from app.web.deps import get_web_user, web_require_roles

router = APIRouter(prefix="/compliance", tags=["web-compliance"])
require_manager = web_require_roles(UserRole.ADMIN, UserRole.FLEET_MANAGER)
INSURANCE_TYPES = {ComplianceType.CIVIL_LIABILITY, ComplianceType.CASCO}
FORM_FIELDS = ("start_date", "end_date", "amount", "provider", "policy_number", "notes")


def _ctx(request: Request, user: User, **values) -> dict:
    return {
        "request": request,
        "user": user,
        "active_page": "compliance",
        "can_edit": user.role in (UserRole.ADMIN, UserRole.FLEET_MANAGER),
        **values,
        **request.app.state.template_globals(request),
    }


def _type_options(request: Request, vehicle_type) -> list[dict]:
    t = request.app.state.template_globals(request)["_"]
    return [{"code": c, "label": t("compliance_type." + c)} for c in compliance_types_for(vehicle_type)]


def _vehicle_label(vehicle: Vehicle) -> str:
    return f"{vehicle.license_plate} — {vehicle.brand} {vehicle.model}"


@router.get("", response_class=HTMLResponse)
async def compliance_list(
    request: Request,
    q: str | None = None,
    document_type: str | None = None,
    status: str | None = None,
    include_history: bool = False,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    status = status if status in STATUSES else None
    document_type = document_type if document_type in set(ComplianceType) else None
    items, total = await ComplianceService(db).list_entries(
        q=q or None,
        document_type=document_type,
        status=status,
        current_only=not include_history,
        page=page,
        size=size,
    )
    partial = request.headers.get("HX-Request") == "true" and request.headers.get("HX-Target") == "compliance-table"
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="compliance/partials/table.html" if partial else "compliance/list.html",
        context=_ctx(
            request,
            user,
            items=items,
            total=total,
            page=page,
            size=size,
            pages=BaseRepository.calc_pages(total, size),
            filters={
                "q": q or "",
                "document_type": document_type,
                "status": status,
                "include_history": include_history,
            },
            filter_query=urlencode(
                {
                    k: v
                    for k, v in {
                        "q": q,
                        "document_type": document_type,
                        "status": status,
                        "include_history": "true" if include_history else None,
                    }.items()
                    if v
                }
            ),
            types=list(ComplianceType),
            statuses=STATUSES,
        ),
    )


@router.get("/vehicle-options", response_class=HTMLResponse)
async def vehicle_options(request: Request, q: str | None = None, db: AsyncSession = Depends(get_db)):
    """HTMX partial for the searchable vehicle picker."""
    user = await get_web_user(request, db)
    if not user:
        return HTMLResponse("", status_code=401)
    vehicles = await ComplianceService(db).vehicle_options(q)
    options = [
        {"vehicle": v, "label": _vehicle_label(v), "types": _type_options(request, v.vehicle_type)} for v in vehicles
    ]
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="compliance/partials/vehicle_options.html",
        context=_ctx(request, user, options=options),
    )


async def _render_form(
    request: Request,
    db: AsyncSession,
    user: User,
    *,
    mode: str,
    form_data: dict,
    source=None,
    error: str | None = None,
    field_errors: dict | None = None,
):
    vehicle = None
    if source is not None:
        vehicle = source.vehicle
    elif form_data.get("vehicle_id"):
        try:
            vehicle = await db.get(Vehicle, UUID(str(form_data["vehicle_id"])))
        except ValueError:
            vehicle = None
    types = _type_options(request, vehicle.vehicle_type) if vehicle else []
    action = {"create": "/compliance/new"}.get(mode) or f"/compliance/{source.id}/{mode}"
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="compliance/form.html",
        status_code=422 if error else 200,
        context=_ctx(
            request,
            user,
            mode=mode,
            action=action,
            source=source,
            form_data=form_data,
            picker={
                "vehicleId": str(vehicle.id) if vehicle else "",
                "label": _vehicle_label(vehicle) if vehicle else "",
                "types": types,
                "selectedType": str(form_data.get("document_type") or ""),
            },
            error=error,
            field_errors=field_errors or {},
        ),
    )


def _errors(exc: Exception, lang: str) -> tuple[str, dict]:
    field_errors = {}
    if isinstance(exc, ValidationError):
        for detail in exc.errors():
            key = detail.get("ctx", {}).get("error")
            name = str(detail["loc"][0]) if detail["loc"] else "end_date"
            field_errors[name] = render_error(key or "errors.invalid_input", lang)
        first = next(iter(field_errors.values()), None)
        return first or render_error(exc, lang), field_errors
    return render_error(exc, lang), field_errors


def _submitted(form) -> dict:
    return {k: (v if v != "" else None) for k, v in form.items() if isinstance(v, str)}


@router.get("/new", response_class=HTMLResponse)
async def compliance_new(
    request: Request,
    vehicle_id: str | None = None,
    document_type: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
):
    return await _render_form(
        request, db, user, mode="create", form_data={"vehicle_id": vehicle_id, "document_type": document_type}
    )


@router.post("/new", response_class=HTMLResponse)
async def compliance_create(
    request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)
):
    raw = _submitted(await request.form())
    try:
        view = await ComplianceService(db).create(ComplianceCreate(**raw), user.id)
    except (ValidationError, DomainError) as exc:
        error, field_errors = _errors(exc, request.state.locale)
        return await _render_form(
            request, db, user, mode="create", form_data=raw, error=error, field_errors=field_errors
        )
    return RedirectResponse(url=f"/compliance/{view.id}", status_code=302)


@router.get("/{entry_id}", response_class=HTMLResponse)
async def compliance_detail(request: Request, entry_id: UUID, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    service = ComplianceService(db)
    view = await service.get(entry_id)
    if not view:
        return RedirectResponse(url="/compliance", status_code=302)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="compliance/detail.html",
        context=_ctx(
            request,
            user,
            entry=view,
            successor=await service.successor(entry_id),
            history_count=await service.history_count(entry_id),
            upload_doc_type="insurance" if view.document_type in INSURANCE_TYPES else "scan",
        ),
    )


async def _source(db: AsyncSession, entry_id: UUID):
    return await ComplianceService(db).get(entry_id)


@router.get("/{entry_id}/edit", response_class=HTMLResponse)
async def compliance_edit(
    request: Request, entry_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)
):
    source = await _source(db, entry_id)
    if not source:
        return RedirectResponse(url="/compliance", status_code=302)
    form_data = {name: getattr(source, name) for name in FORM_FIELDS}
    form_data["document_type"] = source.document_type
    return await _render_form(request, db, user, mode="edit", form_data=form_data, source=source)


@router.post("/{entry_id}/edit", response_class=HTMLResponse)
async def compliance_update(
    request: Request, entry_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)
):
    source = await _source(db, entry_id)
    if not source:
        return RedirectResponse(url="/compliance", status_code=302)
    raw = _submitted(await request.form())
    values = {name: raw.get(name) for name in FORM_FIELDS}
    try:
        await ComplianceService(db).update(entry_id, ComplianceUpdate(**values))
    except (ValidationError, DomainError) as exc:
        error, field_errors = _errors(exc, request.state.locale)
        raw["document_type"] = source.document_type
        return await _render_form(
            request, db, user, mode="edit", form_data=raw, source=source, error=error, field_errors=field_errors
        )
    return RedirectResponse(url=f"/compliance/{entry_id}", status_code=302)


@router.get("/{entry_id}/renew", response_class=HTMLResponse)
async def compliance_renew_form(
    request: Request, entry_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)
):
    service = ComplianceService(db)
    source = await service.get(entry_id)
    if not source:
        return RedirectResponse(url="/compliance", status_code=302)
    form_data = {**service.renewal_proposal(source), "document_type": source.document_type}
    return await _render_form(request, db, user, mode="renew", form_data=form_data, source=source)


@router.post("/{entry_id}/renew", response_class=HTMLResponse)
async def compliance_renew(
    request: Request, entry_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)
):
    service = ComplianceService(db)
    source = await service.get(entry_id)
    if not source:
        return RedirectResponse(url="/compliance", status_code=302)
    raw = _submitted(await request.form())
    try:
        view = await service.renew(entry_id, ComplianceRenew(**{n: raw.get(n) for n in FORM_FIELDS}), user.id)
    except (ValidationError, DomainError) as exc:
        error, field_errors = _errors(exc, request.state.locale)
        raw["document_type"] = source.document_type
        return await _render_form(
            request, db, user, mode="renew", form_data=raw, source=source, error=error, field_errors=field_errors
        )
    return RedirectResponse(url=f"/compliance/{view.id}", status_code=302)


@router.post("/{entry_id}/delete")
async def compliance_delete(
    request: Request, entry_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)
):
    source = await _source(db, entry_id)
    target = f"/vehicles/{source.vehicle_id}" if source else "/compliance"
    scope = (await request.form()).get("scope", "entry")
    try:
        await ComplianceService(db).delete(entry_id, scope)
    except DomainError:
        pass
    return RedirectResponse(url=target, status_code=303)
