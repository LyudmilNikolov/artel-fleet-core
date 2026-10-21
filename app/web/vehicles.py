from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.vehicle import BodyType, FuelType, TransmissionType, VehicleStatus
from app.repositories.base import BaseRepository
from app.schemas.vehicle import VehicleCreate, VehicleUpdate
from app.services.contract_service import ContractService
from app.services.errors import render_error
from app.services.expense_service import ExpenseService
from app.services.maintenance_service import MaintenanceService
from app.services.mileage_service import MileageService
from app.services.vehicle_service import VehicleService
from app.web.deps import get_web_user

router = APIRouter(prefix="/vehicles", tags=["web-vehicles"])


@router.get("", response_class=HTMLResponse)
async def vehicle_list(
    request: Request,
    q: str | None = None,
    status: str | None = None,
    brand: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    status_enum = VehicleStatus(status) if status else None
    service = VehicleService(db)
    vehicles, total = await service.list_vehicles(q=q, status=status_enum, brand=brand, page=page, size=size)
    pages = BaseRepository.calc_pages(total, size)

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="vehicles/list.html",
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
            "status_filter": status_enum,
            "brand_filter": brand,
            "statuses": VehicleStatus,
            **request.app.state.template_globals(request),
        },
    )


@router.get("/new", response_class=HTMLResponse)
async def vehicle_form_new(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="vehicles/form.html",
        context={
            "request": request,
            "user": user,
            "active_page": "vehicles",
            "vehicle": None,
            "body_types": BodyType,
            "fuel_types": FuelType,
            "transmission_types": TransmissionType,
            "statuses": VehicleStatus,
            "error": None,
            **request.app.state.template_globals(request),
        },
    )


@router.post("/new", response_class=HTMLResponse)
async def vehicle_create(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    form = await request.form()
    try:
        data = VehicleCreate(
            license_plate=form.get("license_plate", ""),
            vin=form.get("vin", ""),
            brand=form.get("brand", ""),
            model=form.get("model", ""),
            year=int(form.get("year", 2024)),
            color=form.get("color") or None,
            body_type=form.get("body_type", "sedan"),
            fuel_type=form.get("fuel_type", "gasoline"),
            engine_volume=float(form.get("engine_volume")) if form.get("engine_volume") else None,
            transmission=form.get("transmission", "automatic"),
            seats=int(form.get("seats")) if form.get("seats") else None,
            purchase_price=form.get("purchase_price") or None,
            department=form.get("department") or None,
            notes=form.get("notes") or None,
        )
        service = VehicleService(db)
        vehicle = await service.create(data)
        return RedirectResponse(url=f"/vehicles/{vehicle.id}", status_code=302)
    except (ValueError, Exception) as e:
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="vehicles/form.html",
            context={
                "request": request,
                "user": user,
                "active_page": "vehicles",
                "vehicle": None,
                "body_types": BodyType,
                "fuel_types": FuelType,
                "transmission_types": TransmissionType,
                "statuses": VehicleStatus,
                "error": render_error(e, request.state.locale),
                **request.app.state.template_globals(request),
            },
        )


@router.get("/{vehicle_id}", response_class=HTMLResponse)
async def vehicle_detail(request: Request, vehicle_id: UUID, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    service = VehicleService(db)
    vehicle = await service.get_by_id(vehicle_id)
    if not vehicle:
        return RedirectResponse(url="/vehicles", status_code=302)

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="vehicles/detail.html",
        context={
            "request": request,
            "user": user,
            "active_page": "vehicles",
            "vehicle": vehicle,
            **request.app.state.template_globals(request),
        },
    )


@router.get("/{vehicle_id}/edit", response_class=HTMLResponse)
async def vehicle_form_edit(request: Request, vehicle_id: UUID, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    service = VehicleService(db)
    vehicle = await service.get_by_id(vehicle_id)
    if not vehicle:
        return RedirectResponse(url="/vehicles", status_code=302)

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="vehicles/form.html",
        context={
            "request": request,
            "user": user,
            "active_page": "vehicles",
            "vehicle": vehicle,
            "body_types": BodyType,
            "fuel_types": FuelType,
            "transmission_types": TransmissionType,
            "statuses": VehicleStatus,
            "error": None,
            **request.app.state.template_globals(request),
        },
    )


@router.post("/{vehicle_id}/edit", response_class=HTMLResponse)
async def vehicle_update(request: Request, vehicle_id: UUID, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    form = await request.form()
    update_data = {}
    for field in [
        "license_plate",
        "vin",
        "brand",
        "model",
        "color",
        "body_type",
        "fuel_type",
        "transmission",
        "department",
        "notes",
        "status",
    ]:
        val = form.get(field)
        if val is not None and val != "":
            update_data[field] = val
    for field in ["year", "seats"]:
        val = form.get(field)
        if val:
            update_data[field] = int(val)
    if form.get("engine_volume"):
        update_data["engine_volume"] = float(form["engine_volume"])
    if form.get("purchase_price"):
        update_data["purchase_price"] = form["purchase_price"]

    try:
        data = VehicleUpdate(**update_data)
        service = VehicleService(db)
        await service.update(vehicle_id, data)
        return RedirectResponse(url=f"/vehicles/{vehicle_id}", status_code=302)
    except (ValueError, Exception) as e:
        service = VehicleService(db)
        vehicle = await service.get_by_id(vehicle_id)
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="vehicles/form.html",
            context={
                "request": request,
                "user": user,
                "active_page": "vehicles",
                "vehicle": vehicle,
                "body_types": BodyType,
                "fuel_types": FuelType,
                "transmission_types": TransmissionType,
                "statuses": VehicleStatus,
                "error": render_error(e, request.state.locale),
                **request.app.state.template_globals(request),
            },
        )


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
    service = MaintenanceService(db)
    records, total = await service.list_for_vehicle(vehicle_id, limit=30)
    templates = request.app.state.templates
    ctx = request.app.state.template_globals(request)
    return templates.TemplateResponse(
        request=request,
        name="vehicles/partials/tab_maintenance.html",
        context={"request": request, "records": records, "total": total, "vehicle_id": str(vehicle_id), **ctx},
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
    service = ExpenseService(db)
    records, total = await service.list_for_vehicle(vehicle_id, limit=30)
    templates = request.app.state.templates
    ctx = request.app.state.template_globals(request)
    return templates.TemplateResponse(
        request=request,
        name="vehicles/partials/tab_expenses.html",
        context={"request": request, "records": records, "total": total, "vehicle_id": str(vehicle_id), **ctx},
    )


@router.get("/{vehicle_id}/tab/contracts", response_class=HTMLResponse)
async def vehicle_tab_contracts(
    request: Request,
    vehicle_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    user = await get_web_user(request, db)
    if not user:
        return HTMLResponse("", status_code=401)
    service = ContractService(db)
    records, total = await service.list_for_vehicle(vehicle_id, limit=30)
    templates = request.app.state.templates
    ctx = request.app.state.template_globals(request)
    return templates.TemplateResponse(
        request=request,
        name="vehicles/partials/tab_contracts.html",
        context={"request": request, "records": records, "total": total, "vehicle_id": str(vehicle_id), **ctx},
    )
