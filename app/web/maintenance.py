from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.maintenance import MaintenanceStatus, MaintenanceType
from app.models.vehicle import Vehicle
from app.repositories.base import BaseRepository
from app.schemas.maintenance import MaintenanceCreate
from app.services.errors import render_error
from app.services.maintenance_service import MaintenanceService
from app.web.deps import get_web_user

router = APIRouter(prefix="/maintenance", tags=["web-maintenance"])


@router.get("", response_class=HTMLResponse)
async def maintenance_list(
    request: Request,
    status: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    status_enum = MaintenanceStatus(status) if status else None
    service = MaintenanceService(db)
    items, total = await service.list_all(status=status_enum, page=page, size=size)
    pages = BaseRepository.calc_pages(total, size)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="maintenance/list.html",
        context={
            "request": request,
            "user": user,
            "active_page": "maintenance",
            "records": items,
            "total": total,
            "page": page,
            "size": size,
            "pages": pages,
            "status_filter": status_enum,
            "statuses": MaintenanceStatus,
            **request.app.state.template_globals(request),
        },
    )


@router.get("/new", response_class=HTMLResponse)
async def maintenance_form_new(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="maintenance/form.html",
        context={
            "request": request,
            "user": user,
            "active_page": "maintenance",
            "record": None,
            "vehicles": (await db.scalars(select(Vehicle).order_by(Vehicle.license_plate))).all(),
            "types": MaintenanceType,
            "statuses": MaintenanceStatus,
            "error": None,
            **request.app.state.template_globals(request),
        },
    )


@router.post("/new", response_class=HTMLResponse)
async def maintenance_create(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    form = await request.form()
    try:
        data = MaintenanceCreate(
            vehicle_id=form.get("vehicle_id"),
            type=form.get("type", "scheduled_service"),
            title=form.get("title", ""),
            description=form.get("description") or None,
            scheduled_date=form.get("scheduled_date") or None,
            cost=form.get("cost") or None,
            service_provider=form.get("service_provider") or None,
        )
        if await db.get(Vehicle, data.vehicle_id) is None:
            raise ValueError("errors.invalid_input")
        service = MaintenanceService(db)
        record = await service.create(data, user.id)
        await db.commit()
        return RedirectResponse(url=f"/maintenance/{record.id}", status_code=303)
    except (ValueError, IntegrityError) as e:
        await db.rollback()
        await db.refresh(user)
        error = render_error(
            ValueError("errors.invalid_input") if isinstance(e, IntegrityError) else e, request.state.locale
        )
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="maintenance/form.html",
            context={
                "request": request,
                "user": user,
                "active_page": "maintenance",
                "record": None,
                "vehicles": (await db.scalars(select(Vehicle).order_by(Vehicle.license_plate))).all(),
                "types": MaintenanceType,
                "statuses": MaintenanceStatus,
                "error": error,
                **request.app.state.template_globals(request),
            },
            status_code=400,
        )


@router.get("/{record_id}", response_class=HTMLResponse)
async def maintenance_detail(request: Request, record_id: UUID, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    service = MaintenanceService(db)
    record = await service.get_by_id(record_id)
    if not record:
        return RedirectResponse(url="/maintenance", status_code=302)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="maintenance/detail.html",
        context={
            "request": request,
            "user": user,
            "active_page": "maintenance",
            "record": record,
            **request.app.state.template_globals(request),
        },
    )
