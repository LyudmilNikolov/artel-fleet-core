from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.config import settings
from app.maintenance_rules import DUE_STATUSES, SERVICE_CHECKLIST, SERVICE_ITEMS
from app.models.maintenance import MaintenanceStatus, MaintenanceType
from app.models.user import User, UserRole
from app.models.vehicle import Vehicle
from app.repositories.base import BaseRepository
from app.schemas.maintenance import MaintenanceCreate, MaintenanceUpdate
from app.services.errors import DomainError, MileageBelowCurrentError, render_error
from app.services.maintenance_service import PENDING_STATUSES, MaintenanceService
from app.services.service_due_service import ServiceDueService
from app.services.vehicle_service import VehicleService
from app.vehicle_rules import LIST_TYPE_FILTERS, vehicle_type_filter
from app.web.deps import driver_scope_of, get_web_user, web_require_roles
from app.web.pickers import option_rows, picker_state

router = APIRouter(prefix="/maintenance", tags=["web-maintenance"])
require_manager = web_require_roles(UserRole.ADMIN, UserRole.FLEET_MANAGER)
FORM_FIELDS = (
    "type",
    "title",
    "status",
    "scheduled_date",
    "completed_date",
    "cost",
    "service_provider",
    "mileage_at_service",
    "description",
    "service_item",
    "service_checklist",
    "next_service_mileage",
    "next_service_date",
)
PLANNED_ONLY_FIELDS = ("service_item", "service_checklist", "next_service_mileage", "next_service_date")


def _ctx(request: Request, user: User, **values) -> dict:
    return {
        "request": request,
        "user": user,
        "active_page": "maintenance",
        "can_edit": user.role in (UserRole.ADMIN, UserRole.FLEET_MANAGER),
        **values,
        **request.app.state.template_globals(request),
    }


def _submitted(form) -> dict:
    """Form fields with blanks as None; the checklist checkboxes are collected into a list."""
    values = {k: (v if v != "" else None) for k, v in form.items() if isinstance(v, str)}
    values["service_checklist"] = form.getlist("service_checklist") or None
    return values


async def _render_form(
    request: Request,
    db: AsyncSession,
    user: User,
    *,
    form_data: dict,
    record=None,
    error: str | None = None,
    mileage_warning: MileageBelowCurrentError | None = None,
):
    vehicle = record.vehicle if record else None
    if vehicle is None and form_data.get("vehicle_id"):
        try:
            vehicle = await db.get(Vehicle, UUID(str(form_data["vehicle_id"])))
        except ValueError:
            vehicle = None
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="maintenance/form.html",
        status_code=409 if mileage_warning else 400 if error else 200,
        context=_ctx(
            request,
            user,
            record=record,
            action=f"/maintenance/{record.id}/edit" if record else "/maintenance/new",
            form_data=form_data,
            picker=picker_state(vehicle),
            types=MaintenanceType,
            statuses=MaintenanceStatus,
            service_items=SERVICE_ITEMS,
            checklist_options=SERVICE_CHECKLIST,
            item_labels={
                code: request.app.state.template_globals(request)["_"]("service_item." + code) for code in SERVICE_ITEMS
            },
            error=error,
            mileage_warning=mileage_warning,
        ),
    )


async def _discard_failed_write(db: AsyncSession, user: User, exc: Exception) -> None:
    """Only a database error leaves a failed transaction behind; validation errors wrote nothing."""
    if isinstance(exc, IntegrityError):
        await db.rollback()
        await db.refresh(user)


def _error_text(exc: Exception, request: Request) -> str:
    if isinstance(exc, (ValidationError, IntegrityError)):
        key = (
            next((d.get("ctx", {}).get("error") for d in exc.errors()), None)
            if isinstance(exc, ValidationError)
            else None
        )
        return render_error(key or "errors.invalid_input", request.state.locale)
    return render_error(exc, request.state.locale)


@router.get("", response_class=HTMLResponse)
async def maintenance_list(
    request: Request,
    status: str | None = None,
    when: str | None = None,
    q: str | None = None,
    vehicle_type: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    # "pending" = scheduled and in progress together.
    if status == "pending":
        statuses = list(PENDING_STATUSES)
    else:
        try:
            statuses = [MaintenanceStatus(status)] if status else []
        except ValueError:
            statuses, status = [], None
    when = when if when in ("overdue", "upcoming") else None
    try:
        types = vehicle_type_filter(vehicle_type)
    except ValueError:
        types, vehicle_type = None, None
    items, total = await MaintenanceService(db).list_all(
        status=statuses,
        q=q,
        vehicle_types=types,
        vehicle_ids=driver_scope_of(request).vehicle_filter,
        when=when,
        page=page,
        size=size,
    )
    partial = request.headers.get("HX-Request") == "true" and request.headers.get("HX-Target") == "maintenance-results"
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="maintenance/partials/list_results.html" if partial else "maintenance/list.html",
        context=_ctx(
            request,
            user,
            records=items,
            total=total,
            page=page,
            size=size,
            pages=BaseRepository.calc_pages(total, size),
            q=q or "",
            type_filter=vehicle_type or "",
            type_filters=LIST_TYPE_FILTERS,
            status_filter=status or "",
            when_filter=when or "",
            statuses=MaintenanceStatus,
            upcoming_days=settings.MAINTENANCE_UPCOMING_DAYS,
        ),
    )


@router.get("/due", response_class=HTMLResponse)
async def service_due_list(request: Request, status: str | None = None, db: AsyncSession = Depends(get_db)):
    """Current planned services (engine oil, gearbox oil, ...) with their next due km and date."""
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    status = status if status in DUE_STATUSES else None
    views = await ServiceDueService(db).list_due(status, vehicle_ids=driver_scope_of(request).vehicle_filter)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="maintenance/due.html",
        context=_ctx(request, user, views=views, status_filter=status, due_statuses=DUE_STATUSES),
    )


@router.get("/vehicle-options", response_class=HTMLResponse)
async def vehicle_options(request: Request, q: str | None = None, db: AsyncSession = Depends(get_db)):
    """HTMX partial for the searchable vehicle picker."""
    user = await get_web_user(request, db)
    if not user:
        return HTMLResponse("", status_code=401)
    vehicles = await VehicleService(db).picker_options(q)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="components/vehicle_picker_options.html",
        context=_ctx(request, user, options=option_rows(vehicles)),
    )


@router.get("/new", response_class=HTMLResponse)
async def maintenance_form_new(
    request: Request,
    vehicle_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
):
    return await _render_form(request, db, user, form_data={"vehicle_id": vehicle_id, "status": "scheduled"})


@router.post("/new", response_class=HTMLResponse)
async def maintenance_create(
    request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)
):
    raw = _submitted(await request.form())
    try:
        if not raw.get("vehicle_id"):
            raise DomainError("error.maintenance_vehicle_required")
        data = MaintenanceCreate(
            vehicle_id=raw.get("vehicle_id"),
            type=raw.get("type") or "scheduled_service",
            title=raw.get("title") or "",
            status=raw.get("status") or "scheduled",
            description=raw.get("description"),
            service_item=raw.get("service_item"),
            service_checklist=raw.get("service_checklist"),
            next_service_mileage=raw.get("next_service_mileage"),
            next_service_date=raw.get("next_service_date"),
            scheduled_date=raw.get("scheduled_date"),
            completed_date=raw.get("completed_date"),
            cost=raw.get("cost"),
            service_provider=raw.get("service_provider"),
            mileage_at_service=raw.get("mileage_at_service"),
            overwrite_mileage=raw.get("confirm_mileage_overwrite") == "1",
        )
        record = await MaintenanceService(db).create(data, user.id)
        await db.commit()
    except MileageBelowCurrentError as exc:
        return await _render_form(request, db, user, form_data=raw, mileage_warning=exc)
    except (ValidationError, DomainError, IntegrityError) as exc:
        await _discard_failed_write(db, user, exc)
        return await _render_form(request, db, user, form_data=raw, error=_error_text(exc, request))
    return RedirectResponse(url=f"/maintenance/{record.id}", status_code=303)


@router.get("/{record_id}", response_class=HTMLResponse)
async def maintenance_detail(request: Request, record_id: UUID, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    record = await MaintenanceService(db).get_by_id(record_id)
    if not record or not driver_scope_of(request).allows_vehicle(record.vehicle_id):
        return RedirectResponse(url="/maintenance", status_code=302)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="maintenance/detail.html",
        context=_ctx(request, user, record=record, due=await ServiceDueService(db).state_for_record(record)),
    )


@router.get("/{record_id}/edit", response_class=HTMLResponse)
async def maintenance_edit(
    request: Request, record_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)
):
    record = await MaintenanceService(db).get_by_id(record_id)
    if not record:
        return RedirectResponse(url="/maintenance", status_code=302)
    form_data = {name: getattr(record, name) for name in FORM_FIELDS}
    form_data.update(type=record.type.value, status=record.status.value, vehicle_id=str(record.vehicle_id))
    return await _render_form(request, db, user, form_data=form_data, record=record)


@router.post("/{record_id}/edit", response_class=HTMLResponse)
async def maintenance_update(
    request: Request, record_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)
):
    service = MaintenanceService(db)
    record = await service.get_by_id(record_id)
    if not record:
        return RedirectResponse(url="/maintenance", status_code=302)
    raw = _submitted(await request.form())
    try:
        new_type = raw.get("type") or record.type.value
        # The item and next due km / date are only offered for planned services; other types keep what they have.
        fields = [n for n in FORM_FIELDS if new_type == "scheduled_service" or n not in PLANNED_ONLY_FIELDS]
        data = MaintenanceUpdate(
            **{name: raw.get(name) for name in fields if name not in ("type", "status") or raw.get(name)},
            overwrite_mileage=raw.get("confirm_mileage_overwrite") == "1",
        )
        await service.update(record_id, data)
        await db.commit()
    except MileageBelowCurrentError as exc:
        raw["vehicle_id"] = str(record.vehicle_id)
        return await _render_form(request, db, user, form_data=raw, record=record, mileage_warning=exc)
    except (ValidationError, DomainError, IntegrityError) as exc:
        await _discard_failed_write(db, user, exc)
        record = await service.get_by_id(record_id)
        raw["vehicle_id"] = str(record.vehicle_id)
        return await _render_form(request, db, user, form_data=raw, record=record, error=_error_text(exc, request))
    return RedirectResponse(url=f"/maintenance/{record_id}", status_code=303)


@router.post("/{record_id}/complete")
async def maintenance_complete(
    request: Request, record_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)
):
    try:
        await MaintenanceService(db).complete(record_id)
    except DomainError:
        return RedirectResponse(url="/maintenance", status_code=303)
    target = (await request.form()).get("next") or f"/maintenance/{record_id}"
    return RedirectResponse(
        url=target if target.startswith("/maintenance") else f"/maintenance/{record_id}", status_code=303
    )


@router.post("/{record_id}/delete")
async def maintenance_delete(
    request: Request, record_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)
):
    try:
        await MaintenanceService(db).delete(record_id)
    except DomainError:
        pass
    return RedirectResponse(url="/maintenance", status_code=303)
