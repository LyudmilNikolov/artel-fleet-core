from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.driver import DriverStatus
from app.repositories.base import BaseRepository
from app.schemas.driver import DriverCreate, DriverUpdate
from app.services.driver_service import DriverService
from app.services.errors import render_error
from app.web.deps import get_web_user

router = APIRouter(prefix="/drivers", tags=["web-drivers"])


@router.get("", response_class=HTMLResponse)
async def driver_list(
    request: Request,
    q: str | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    status_enum = DriverStatus(status) if status else None
    service = DriverService(db)
    drivers, total = await service.list_drivers(q=q, status=status_enum, page=page, size=size)
    pages = BaseRepository.calc_pages(total, size)

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="drivers/list.html",
        context={
            "request": request,
            "user": user,
            "active_page": "drivers",
            "drivers": drivers,
            "total": total,
            "page": page,
            "size": size,
            "pages": pages,
            "q": q,
            "status_filter": status_enum,
            "statuses": DriverStatus,
            **request.app.state.template_globals(request),
        },
    )


@router.get("/new", response_class=HTMLResponse)
async def driver_form_new(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="drivers/form.html",
        context={
            "request": request,
            "user": user,
            "active_page": "drivers",
            "driver": None,
            "statuses": DriverStatus,
            "error": None,
            **request.app.state.template_globals(request),
        },
    )


@router.post("/new", response_class=HTMLResponse)
async def driver_create(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    form = await request.form()
    try:
        data = DriverCreate(
            full_name=form.get("full_name", ""),
            employee_id=form.get("employee_id") or None,
            phone=form.get("phone") or None,
            email=form.get("email") or None,
            license_number=form.get("license_number") or None,
            license_category=form.get("license_category") or None,
            department=form.get("department") or None,
            notes=form.get("notes") or None,
        )
        service = DriverService(db)
        driver = await service.create(data)
        return RedirectResponse(url=f"/drivers/{driver.id}", status_code=302)
    except (ValueError, Exception) as e:
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="drivers/form.html",
            context={
                "request": request,
                "user": user,
                "active_page": "drivers",
                "driver": None,
                "statuses": DriverStatus,
                "error": render_error(e, request.state.locale),
                **request.app.state.template_globals(request),
            },
        )


@router.get("/{driver_id}", response_class=HTMLResponse)
async def driver_detail(request: Request, driver_id: UUID, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    service = DriverService(db)
    driver = await service.get_by_id(driver_id)
    if not driver:
        return RedirectResponse(url="/drivers", status_code=302)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="drivers/detail.html",
        context={
            "request": request,
            "user": user,
            "active_page": "drivers",
            "driver": driver,
            **request.app.state.template_globals(request),
        },
    )


@router.get("/{driver_id}/edit", response_class=HTMLResponse)
async def driver_form_edit(request: Request, driver_id: UUID, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    service = DriverService(db)
    driver = await service.get_by_id(driver_id)
    if not driver:
        return RedirectResponse(url="/drivers", status_code=302)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="drivers/form.html",
        context={
            "request": request,
            "user": user,
            "active_page": "drivers",
            "driver": driver,
            "statuses": DriverStatus,
            "error": None,
            **request.app.state.template_globals(request),
        },
    )


@router.post("/{driver_id}/edit", response_class=HTMLResponse)
async def driver_update(request: Request, driver_id: UUID, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    form = await request.form()
    update_data = {}
    for field in [
        "full_name",
        "employee_id",
        "phone",
        "email",
        "license_number",
        "license_category",
        "department",
        "notes",
        "status",
    ]:
        val = form.get(field)
        if val is not None and val != "":
            update_data[field] = val
    try:
        data = DriverUpdate(**update_data)
        service = DriverService(db)
        await service.update(driver_id, data)
        return RedirectResponse(url=f"/drivers/{driver_id}", status_code=302)
    except (ValueError, Exception) as e:
        service = DriverService(db)
        driver = await service.get_by_id(driver_id)
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="drivers/form.html",
            context={
                "request": request,
                "user": user,
                "active_page": "drivers",
                "driver": driver,
                "statuses": DriverStatus,
                "error": render_error(e, request.state.locale),
                **request.app.state.template_globals(request),
            },
        )
