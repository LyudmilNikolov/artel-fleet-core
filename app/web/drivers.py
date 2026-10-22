from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.driver_document_rules import mask_number
from app.models.driver import DriverStatus
from app.models.user import User, UserRole
from app.repositories.base import BaseRepository
from app.repositories.user_repo import UserRepository
from app.schemas.driver import DriverCreate, DriverUpdate
from app.services.driver_access import (
    can_edit_driver_documents,
    can_read_driver_documents,
    can_see_numbers,
    can_view_scans_of,
)
from app.services.driver_document_service import DriverDocumentService
from app.services.driver_service import DriverService
from app.services.errors import DomainError, render_error
from app.services.vehicle_service import VehicleService
from app.web.deps import get_web_user, web_require_roles
from app.web.pickers import option_rows, picker_state

router = APIRouter(prefix="/drivers", tags=["web-drivers"])
require_manager = web_require_roles(UserRole.ADMIN, UserRole.FLEET_MANAGER)
FORM_FIELDS = ("full_name", "phone", "email", "hire_date", "department", "notes")


def _ctx(request: Request, user: User, **values) -> dict:
    return {
        "request": request,
        "user": user,
        "active_page": "drivers",
        **values,
        **request.app.state.template_globals(request),
    }


def _form_values(form) -> dict:
    """Submitted form fields with blanks as None."""
    return {name: (str(form.get(name)).strip() or None) if form.get(name) is not None else None for name in FORM_FIELDS}


def _form_response(request: Request, user: User, driver, error: str | None = None, form_data: dict | None = None):
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="drivers/form.html",
        status_code=422 if error else 200,
        context=_ctx(request, user, driver=driver, statuses=DriverStatus, error=error, form_data=form_data or {}),
    )


@router.get("", response_class=HTMLResponse)
async def driver_list(
    request: Request,
    q: str | None = None,
    status: str | None = None,
    doc_status: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    # Default view hides terminated drivers; "all" shows everyone, a status shows just that status.
    status_enum = DriverStatus(status) if status in {s.value for s in DriverStatus} else None
    show_all = status == "all"
    doc_status = doc_status if doc_status in ("expired", "expiring_soon") else None
    service = DriverService(db)
    drivers, total = await service.list_drivers(
        q=q,
        status=status_enum,
        exclude_status=None if (status_enum or show_all) else DriverStatus.TERMINATED,
        doc_status=doc_status,
        page=page,
        size=size,
    )
    # Driver-role users do not see other drivers' document state.
    show_docs = user.role != UserRole.DRIVER
    alerts = await DriverDocumentService(db).alert_counts_by_driver([d.id for d in drivers]) if show_docs else {}
    pages = BaseRepository.calc_pages(total, size)

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="drivers/list.html",
        context=_ctx(
            request,
            user,
            drivers=drivers,
            alerts=alerts,
            show_docs=show_docs,
            total=total,
            page=page,
            size=size,
            pages=pages,
            q=q,
            status_filter=status_enum,
            show_all=show_all,
            doc_status=doc_status,
            statuses=DriverStatus,
            can_edit=user.role in (UserRole.ADMIN, UserRole.FLEET_MANAGER),
        ),
    )


@router.get("/new", response_class=HTMLResponse)
async def driver_form_new(request: Request, user: User = Depends(require_manager)):
    return _form_response(request, user, None)


@router.post("/new", response_class=HTMLResponse)
async def driver_create(request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)):
    values = _form_values(await request.form())
    try:
        driver = await DriverService(db).create(DriverCreate(**values))
    except (ValidationError, DomainError, ValueError) as e:
        return _form_response(request, user, None, render_error(e, request.state.locale), values)
    return RedirectResponse(url=f"/drivers/{driver.id}", status_code=302)


@router.get("/{driver_id}/vehicle-options", response_class=HTMLResponse)
async def driver_vehicle_options(
    request: Request,
    driver_id: UUID,
    q: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
):
    """HTMX partial for the vehicle picker on the driver page."""
    vehicles = await VehicleService(db).picker_options(q)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="components/vehicle_picker_options.html",
        context=_ctx(request, user, options=option_rows(vehicles)),
    )


@router.post("/{driver_id}/vehicles")
async def driver_assign_vehicle(
    request: Request, driver_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)
):
    """Assign a vehicle to the driver (the driver then sees that vehicle when logged in)."""
    form = await request.form()
    try:
        await DriverService(db).assign_vehicle(driver_id, UUID(str(form.get("vehicle_id") or "")))
    except (DomainError, ValueError):
        pass  # an invalid choice leaves the assignments unchanged
    return RedirectResponse(url=f"/drivers/{driver_id}", status_code=303)


@router.post("/{driver_id}/vehicles/{vehicle_id}/unassign")
async def driver_unassign_vehicle(
    driver_id: UUID, vehicle_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)
):
    try:
        await DriverService(db).unassign_vehicle(driver_id, vehicle_id)
    except DomainError:
        pass
    return RedirectResponse(url=f"/drivers/{driver_id}", status_code=303)


@router.get("/{driver_id}", response_class=HTMLResponse)
async def driver_detail(request: Request, driver_id: UUID, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    service = DriverService(db)
    driver = await service.get_by_id(driver_id)
    if not driver:
        return RedirectResponse(url="/drivers", status_code=302)
    can_docs = can_read_driver_documents(user, driver)
    alerts = (
        (await DriverDocumentService(db).alert_counts_by_driver([driver.id])).get(driver.id, {}) if can_docs else {}
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="drivers/detail.html",
        context=_ctx(
            request,
            user,
            driver=driver,
            vehicles=await service.assigned_vehicles(driver.id),
            login_user=await UserRepository(db).get_by_id(driver.user_id) if driver.user_id else None,
            picker=picker_state(None),
            alerts=alerts,
            can_docs=can_docs,
            can_scans=can_view_scans_of(user, driver),
            can_upload=can_edit_driver_documents(user, driver),
            can_edit=user.role in (UserRole.ADMIN, UserRole.FLEET_MANAGER),
        ),
    )


@router.get("/{driver_id}/tab/licences", response_class=HTMLResponse)
async def driver_tab_licences(request: Request, driver_id: UUID, db: AsyncSession = Depends(get_db)):
    """HTMX partial for the Licences & Documents tab: current entry per type, then history."""
    user = await get_web_user(request, db)
    if not user:
        return HTMLResponse("", status_code=401)
    driver = await DriverService(db).get_by_id(driver_id)
    if not driver:
        return HTMLResponse("", status_code=404)
    if not can_read_driver_documents(user, driver):
        return HTMLResponse("", status_code=403)
    summary = await DriverDocumentService(db).summary_for_driver(driver)
    full_numbers = can_see_numbers(user, driver)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="drivers/partials/tab_licences.html",
        context=_ctx(
            request,
            user,
            summary=summary,
            number=lambda value: value if full_numbers else mask_number(value),
            can_edit=can_edit_driver_documents(user, driver),
        ),
    )


@router.get("/{driver_id}/edit", response_class=HTMLResponse)
async def driver_form_edit(
    request: Request, driver_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)
):
    driver = await DriverService(db).get_by_id(driver_id)
    if not driver:
        return RedirectResponse(url="/drivers", status_code=302)
    return _form_response(request, user, driver)


@router.post("/{driver_id}/edit", response_class=HTMLResponse)
async def driver_update(
    request: Request, driver_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)
):
    service = DriverService(db)
    driver = await service.get_by_id(driver_id)
    if not driver:
        return RedirectResponse(url="/drivers", status_code=302)
    form = await request.form()
    values = _form_values(form)
    update_data = {
        k: v for k, v in values.items() if v is not None or k in ("phone", "email", "hire_date", "department", "notes")
    }
    status = form.get("status")
    if status:
        update_data["status"] = status
    try:
        await service.update(driver_id, DriverUpdate(**update_data))
    except (ValidationError, DomainError, ValueError) as e:
        return _form_response(request, user, driver, render_error(e, request.state.locale), values)
    return RedirectResponse(url=f"/drivers/{driver_id}", status_code=302)


@router.post("/{driver_id}/delete")
async def driver_delete(
    request: Request, driver_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)
):
    """Delete a driver with its document entries and uploaded files."""
    try:
        await DriverService(db).delete(driver_id)
    except DomainError:
        pass
    return RedirectResponse(url="/drivers", status_code=303)
