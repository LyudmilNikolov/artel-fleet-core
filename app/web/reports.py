from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.maintenance import MaintenanceStatus
from app.schemas.report import ExpenseReportFilters, MaintenanceReportFilters, ReportFilters, parse_report_filters
from app.services.audit_service import AuditService
from app.services.errors import DomainError, render_error
from app.services.report_service import ReportService
from app.services.vehicle_service import VehicleService
from app.vehicle_rules import LIST_TYPE_FILTERS, vehicle_type_filter
from app.web.deps import get_web_user
from app.web.expenses import LISTED_CATEGORIES
from app.web.pickers import option_rows, picker_state

router = APIRouter(prefix="/reports", tags=["web-reports"])

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
EMPTY_EXPENSES = {"by_category": [], "monthly": [], "grand_total": Decimal("0"), "total_count": 0}
EMPTY_MAINTENANCE = {"rows": [], "total": 0, "total_cost": Decimal("0"), "completed": 0, "pages": 1}


@router.get("", response_class=HTMLResponse)
async def reports_index(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="reports/index.html",
        context={
            "request": request,
            "user": user,
            "active_page": "reports",
            **request.app.state.template_globals(request),
        },
    )


async def _report_page(
    request: Request, db: AsyncSession, template: str, load, empty, cls=ReportFilters, paging: tuple | None = None
):
    """Render a report page; invalid filters give a localized error banner, empty data and HTTP 422."""
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    svc = ReportService(db)
    filters, vehicle, data, error, mismatch = cls(), None, empty, None, False
    try:
        filters = parse_report_filters(request.query_params, cls)
        vehicle = await svc.selected_vehicle(filters)
        mismatch = await svc.vehicle_mismatch(filters)
        data = await load(svc, filters)
    except DomainError as exc:
        error = render_error(exc, request.state.locale)
        filters, vehicle, data = cls(), None, empty
    return request.app.state.templates.TemplateResponse(
        request=request,
        name=template,
        status_code=422 if error else 200,
        context={
            "request": request,
            "user": user,
            "active_page": "reports",
            "data": data,
            "filters": filters,
            "start_date": filters.start_date,
            "end_date": filters.end_date,
            "picker": picker_state(vehicle),
            "type_filters": LIST_TYPE_FILTERS,
            "statuses": [s.value for s in MaintenanceStatus],
            "categories": [c.value for c in LISTED_CATEGORIES],
            "filter_query": filters.to_query(),
            "filter_error": error,
            "vehicle_mismatch": mismatch,
            **(
                {"page": paging[0], "size": paging[1], "pages": data["pages"], "total": data["total"]} if paging else {}
            ),
            **request.app.state.template_globals(request),
        },
    )


@router.get("/tco", response_class=HTMLResponse)
async def report_tco(request: Request, db: AsyncSession = Depends(get_db)):
    return await _report_page(request, db, "reports/tco.html", lambda svc, f: svc.tco_report(f), [])


@router.get("/fuel", response_class=HTMLResponse)
async def report_fuel(request: Request, db: AsyncSession = Depends(get_db)):
    return await _report_page(request, db, "reports/fuel.html", lambda svc, f: svc.fuel_consumption(f), [])


@router.get("/expenses", response_class=HTMLResponse)
async def report_expenses(request: Request, db: AsyncSession = Depends(get_db)):
    return await _report_page(
        request,
        db,
        "reports/expenses.html",
        lambda svc, f: svc.expense_analysis(f),
        EMPTY_EXPENSES,
        ExpenseReportFilters,
    )


@router.get("/maintenance", response_class=HTMLResponse)
async def report_maintenance(
    request: Request,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    return await _report_page(
        request,
        db,
        "reports/maintenance.html",
        lambda svc, f: svc.maintenance_page(f, page, size),
        EMPTY_MAINTENANCE,
        MaintenanceReportFilters,
        paging=(page, size),
    )


@router.get("/vehicle-options", response_class=HTMLResponse)
async def vehicle_options(
    request: Request, q: str | None = None, vehicle_type: str | None = None, db: AsyncSession = Depends(get_db)
):
    """HTMX partial for the report vehicle picker, narrowed to the chosen vehicle type group."""
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    try:
        types = vehicle_type_filter(vehicle_type)
    except ValueError:
        types = None
    vehicles = await VehicleService(db).picker_options(q, vehicle_types=types)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="components/vehicle_picker_options.html",
        context={
            "request": request,
            "user": user,
            "options": option_rows(vehicles),
            **request.app.state.template_globals(request),
        },
    )


# Excel/CSV exports from web UI: same filters as the page they are linked from


async def _export(request: Request, db: AsyncSession, build, filename: str, media_type: str, cls=ReportFilters):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    svc = ReportService(db)
    try:
        filters = parse_report_filters(request.query_params, cls)
        await svc.selected_vehicle(filters)
        content = await build(svc, filters, request.state.locale)
        stem, _, file_format = filename.rpartition(".")
        await AuditService(db).log_export(
            user_id=user.id, report=stem, file_format=file_format, filters=filters.to_query()
        )
    except DomainError as exc:
        return Response(render_error(exc, request.state.locale), status_code=422, media_type="text/plain")
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/export/tco.xlsx")
async def web_export_tco(request: Request, db: AsyncSession = Depends(get_db)):
    return await _export(request, db, lambda s, f, loc: s.export_tco_excel(f, locale=loc), "tco_report.xlsx", XLSX)


@router.get("/export/fuel.xlsx")
async def web_export_fuel(request: Request, db: AsyncSession = Depends(get_db)):
    return await _export(
        request, db, lambda s, f, loc: s.export_fuel_excel(f, locale=loc), "fuel_consumption.xlsx", XLSX
    )


@router.get("/export/maintenance.xlsx")
async def web_export_maintenance(request: Request, db: AsyncSession = Depends(get_db)):
    return await _export(
        request,
        db,
        lambda s, f, loc: s.export_maintenance_excel(f, locale=loc),
        "maintenance_history.xlsx",
        XLSX,
        MaintenanceReportFilters,
    )


@router.get("/export/expenses.csv")
async def web_export_expenses(request: Request, db: AsyncSession = Depends(get_db)):
    return await _export(
        request,
        db,
        lambda s, f, loc: s.export_expense_csv(f, locale=loc),
        "expense_analysis.csv",
        "text/csv",
        ExpenseReportFilters,
    )
