from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.vehicle import FuelType, TransmissionType, VehicleStatus, VehicleType
from app.repositories.base import BaseRepository
from app.schemas.vehicle import VehicleCreate, VehicleRead, VehicleUpdate
from app.services.compliance_service import ComplianceService
from app.services.errors import DomainError, render_error
from app.services.expense_service import ExpenseService
from app.services.maintenance_service import MaintenanceService
from app.services.mileage_service import MileageService
from app.services.service_due_service import ServiceDueService
from app.services.vehicle_service import VehicleService
from app.vehicle_rules import (
    APPLICABLE,
    CONTAINER_SIZES,
    CREATE_DEFAULTS,
    DECIMAL_BOUNDS,
    EMISSION_STANDARDS,
    FUEL_CHOICES,
    INTEGER_BOUNDS,
    LIST_TYPE_FILTERS,
    TRAILER_TYPES,
    PowerSource,
    type_group_of,
    vehicle_type_filter,
)
from app.web.deps import can_contribute, driver_scope_of, get_web_user

router = APIRouter(prefix="/vehicles", tags=["web-vehicles"])

VEHICLE_GROUPS = list(dict.fromkeys(type_group_of(t) for t in VehicleType))




@router.get("", response_class=HTMLResponse)
async def vehicle_list(
    request: Request,
    q: str | None = None,
    status: str | None = None,
    vehicle_type: str | None = None,
    fuel_type: str | None = None,
    department: str | None = None,
    brand: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    try:
        status_enum = VehicleStatus(status) if status else None
        fuel_type = FuelType(fuel_type) if fuel_type else None
        types = vehicle_type_filter(vehicle_type)
    except ValueError:
        return HTMLResponse(render_error("errors.invalid_input", request.state.locale), status_code=422)
    # fuel type, department and brand are not offered in the filter bar but old links keep working.
    vehicles, total = await VehicleService(db).list_vehicles(
        q=q,
        status=status_enum,
        brand=brand or None,
        vehicle_type=types,
        fuel_type=fuel_type,
        department=department or None,
        vehicle_ids=driver_scope_of(request).vehicle_filter,
        page=page,
        size=size,
    )
    pages = BaseRepository.calc_pages(total, size)
    partial = request.headers.get("HX-Request") == "true" and request.headers.get("HX-Target") == "vehicle-results"

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="vehicles/partials/list_results.html" if partial else "vehicles/list.html",
        context={
            "request": request,
            "user": user,
            "active_page": "vehicles",
            "vehicles": vehicles,
            "total": total,
            "page": page,
            "size": size,
            "pages": pages,
            "q": q,
            "status_filter": status_enum.value if status_enum else "",
            "type_filter": vehicle_type or "",
            "statuses": VehicleStatus,
            "type_filters": LIST_TYPE_FILTERS,
            **request.app.state.template_globals(request),
        },
    )


async def render_form(request, db, user, vehicle=None, form_data=None, error=None, field_errors=None):
    if form_data is None:
        form_data = (
            VehicleRead.model_validate(vehicle).model_dump(mode="json")
            if vehicle
            else {"vehicle_type": "tractor_unit", **CREATE_DEFAULTS["tractor_unit"]}
        )
        # An edit reading is deliberately blank; the authoritative value is separate.
        if vehicle:
            form_data["current_mileage"] = ""
    choices = {
        "fuel_type": [(v.value, "fuel_type." + v.value) for v in FuelType],
        "transmission": [(v.value, "transmission." + v.value) for v in TransmissionType],
        "power_source": [(v.value, "power_source." + v.value) for v in PowerSource],
        "emission_standard": [(v, "emission_standard." + v.replace(" ", "_")) for v in EMISSION_STANDARDS],
    }
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="vehicles/form.html",
        status_code=422 if error else 200,
        context={
            "request": request,
            "user": user,
            "active_page": "vehicles",
            "vehicle": vehicle,
            "form_data": form_data,
            "departments": await VehicleService(db).departments(),
            "vehicle_types": VehicleType,
            "vehicle_groups": VEHICLE_GROUPS,
            "trailer_types": TRAILER_TYPES,
            "statuses": VehicleStatus,
            "error": error,
            "field_errors": field_errors or {},
            "choices": choices,
            "container_sizes": CONTAINER_SIZES,
            "technical_fields": sorted(set.union(*APPLICABLE.values())),
            "integer_bounds": INTEGER_BOUNDS,
            "decimal_bounds": DECIMAL_BOUNDS,
            "rules": {
                "applicable": {k: sorted(v) for k, v in APPLICABLE.items()},
                "defaults": CREATE_DEFAULTS,
                "fuel": FUEL_CHOICES,
            },
            "apply_defaults": vehicle is None and error is None,
            **request.app.state.template_globals(request),
        },
    )


def submitted_values(form, editing):
    values = dict(form)
    if form.get("vehicle_type") == "container_semi_trailer":
        values["supported_container_sizes"] = form.getlist("supported_container_sizes")
    for name, value in list(values.items()):
        if value == "":
            if editing and name == "current_mileage":
                del values[name]
            else:
                values[name] = None
    return values


async def save_form(request, db, user, vehicle=None):
    form = await request.form()
    raw = dict(form)
    raw["supported_container_sizes"] = form.getlist("supported_container_sizes")
    try:
        values = submitted_values(form, vehicle is not None)
        if vehicle:
            result = await VehicleService(db).update(vehicle.id, VehicleUpdate(**values), user_id=user.id)
        else:
            result = await VehicleService(db).create(VehicleCreate(**values), user_id=user.id)
        return RedirectResponse(url=f"/vehicles/{result.id}", status_code=302)
    except (ValidationError, DomainError) as exc:
        errors = {}
        if isinstance(exc, ValidationError):
            for detail in exc.errors():
                key = detail.get("ctx", {}).get("error")
                errors[str(detail["loc"][0]) if detail["loc"] else "vehicle_type"] = render_error(
                    key or "errors.invalid_input", request.state.locale
                )
        return await render_form(request, db, user, vehicle, raw, render_error(exc, request.state.locale), errors)


@router.get("/new", response_class=HTMLResponse)
async def vehicle_form_new(request: Request, license_plate: str | None = None, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role.value not in ("admin", "fleet_manager"):
        return RedirectResponse(url="/vehicles", status_code=303)
    form_data = None
    if license_plate and license_plate.strip():  # e.g. an unknown plate from a toll import
        form_data = {"vehicle_type": "tractor_unit", **CREATE_DEFAULTS["tractor_unit"], "license_plate": license_plate.strip()[:20]}
    return await render_form(request, db, user, form_data=form_data)


@router.post("/new", response_class=HTMLResponse)
async def vehicle_create(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role.value not in ("admin", "fleet_manager"):
        return RedirectResponse(url="/vehicles", status_code=303)
    return await save_form(request, db, user)


@router.get("/{vehicle_id}", response_class=HTMLResponse)
async def vehicle_detail(request: Request, vehicle_id: UUID, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    service = VehicleService(db)
    vehicle = await service.get_by_id(vehicle_id)
    if not vehicle or not driver_scope_of(request).allows_vehicle(vehicle.id):
        return RedirectResponse(url="/vehicles", status_code=302)

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="vehicles/detail.html",
        context={
            "request": request,
            "user": user,
            "active_page": "vehicles",
            "vehicle": vehicle,
            "can_upload": can_contribute(request, user),
            "applicable_fields": sorted(APPLICABLE[vehicle.vehicle_type]),
            **request.app.state.template_globals(request),
        },
    )


@router.get("/{vehicle_id}/edit", response_class=HTMLResponse)
async def vehicle_form_edit(request: Request, vehicle_id: UUID, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role.value not in ("admin", "fleet_manager"):
        return RedirectResponse(url="/vehicles", status_code=303)
    vehicle = await VehicleService(db).get_by_id(vehicle_id)
    if not vehicle:
        return RedirectResponse(url="/vehicles", status_code=302)
    return await render_form(request, db, user, vehicle)


@router.post("/{vehicle_id}/edit", response_class=HTMLResponse)
async def vehicle_update(request: Request, vehicle_id: UUID, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role.value not in ("admin", "fleet_manager"):
        return RedirectResponse(url="/vehicles", status_code=303)
    vehicle = await VehicleService(db).get_by_id(vehicle_id)
    if not vehicle:
        return RedirectResponse(url="/vehicles", status_code=302)
    return await save_form(request, db, user, vehicle)


@router.post("/{vehicle_id}/delete")
async def vehicle_delete(request: Request, vehicle_id: UUID, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role.value not in ("admin", "fleet_manager"):
        return RedirectResponse(url=f"/vehicles/{vehicle_id}", status_code=303)
    try:
        await VehicleService(db).delete(vehicle_id)
    except DomainError:
        pass
    return RedirectResponse(url="/vehicles", status_code=303)


# ---- HTMX widget endpoints for vehicle detail tabs ----


@router.get("/{vehicle_id}/tab/mileage", response_class=HTMLResponse)
async def vehicle_tab_mileage(
    request: Request,
    vehicle_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    user = await get_web_user(request, db)
    if not user:
        return HTMLResponse("", status_code=401)
    if not driver_scope_of(request).allows_vehicle(vehicle_id):
        return HTMLResponse("", status_code=403)
    service = MileageService(db)
    logs = await service.get_history(vehicle_id, limit=50)
    templates = request.app.state.templates
    ctx = request.app.state.template_globals(request)
    return templates.TemplateResponse(
        request=request,
        name="vehicles/partials/tab_mileage.html",
        context={"request": request, "logs": logs, "vehicle_id": str(vehicle_id), **ctx},
    )


@router.get("/{vehicle_id}/tab/maintenance", response_class=HTMLResponse)
async def vehicle_tab_maintenance(
    request: Request,
    vehicle_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    user = await get_web_user(request, db)
    if not user:
        return HTMLResponse("", status_code=401)
    if not driver_scope_of(request).allows_vehicle(vehicle_id):
        return HTMLResponse("", status_code=403)
    service = MaintenanceService(db)
    records, total = await service.list_for_vehicle(vehicle_id, limit=30)
    templates = request.app.state.templates
    ctx = request.app.state.template_globals(request)
    return templates.TemplateResponse(
        request=request,
        name="vehicles/partials/tab_maintenance.html",
        context={
            "request": request,
            "records": records,
            "total": total,
            "vehicle_id": str(vehicle_id),
            "due_items": await ServiceDueService(db).for_vehicle(vehicle_id),
            **ctx,
        },
    )


@router.get("/{vehicle_id}/tab/expenses", response_class=HTMLResponse)
async def vehicle_tab_expenses(
    request: Request,
    vehicle_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    user = await get_web_user(request, db)
    if not user:
        return HTMLResponse("", status_code=401)
    if not driver_scope_of(request).allows_vehicle(vehicle_id):
        return HTMLResponse("", status_code=403)
    service = ExpenseService(db)
    records, total = await service.list_for_vehicle(vehicle_id, limit=30)
    templates = request.app.state.templates
    ctx = request.app.state.template_globals(request)
    return templates.TemplateResponse(
        request=request,
        name="vehicles/partials/tab_expenses.html",
        context={
            "request": request,
            "records": records,
            "total": total,
            "vehicle_id": str(vehicle_id),
            "due_items": await ServiceDueService(db).for_vehicle(vehicle_id),
            **ctx,
        },
    )


@router.get("/{vehicle_id}/tab/compliance", response_class=HTMLResponse)
async def vehicle_tab_compliance(
    request: Request,
    vehicle_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    user = await get_web_user(request, db)
    if not user:
        return HTMLResponse("", status_code=401)
    if not driver_scope_of(request).allows_vehicle(vehicle_id):
        return HTMLResponse("", status_code=403)
    vehicle = await VehicleService(db).get_by_id(vehicle_id)
    if not vehicle:
        return HTMLResponse("", status_code=404)
    summary = await ComplianceService(db).summary_for_vehicle(vehicle)
    templates = request.app.state.templates
    ctx = request.app.state.template_globals(request)
    return templates.TemplateResponse(
        request=request,
        name="vehicles/partials/tab_compliance.html",
        context={
            "request": request,
            "summary": summary,
            "can_edit": user.role.value in ("admin", "fleet_manager"),
            "vehicle_id": str(vehicle_id),
            **ctx,
        },
    )
