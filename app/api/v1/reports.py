from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_fleet_manager
from app.database import get_db
from app.models.user import User
from app.schemas.report import (
    ExpenseAnalysis,
    ExpenseReportFilters,
    FuelReportRow,
    MaintenanceReportFilters,
    MaintenanceReportRow,
    ReportFilters,
    TCOReportRow,
    parse_report_filters,
)
from app.services.audit_service import AuditService
from app.services.errors import DomainError
from app.services.report_service import ReportService

router = APIRouter(prefix="/reports", tags=["reports"])

# export file name -> (report, format) recorded in the audit log
REPORT_NAMES = {
    "tco.xlsx": ("tco_report", "xlsx"),
    "fuel.xlsx": ("fuel_consumption", "xlsx"),
    "maintenance.xlsx": ("maintenance_history", "xlsx"),
    "expenses.csv": ("expense_analysis", "csv"),
}


def _parse(raw: dict, cls=ReportFilters):
    try:
        return parse_report_filters(raw, cls)
    except DomainError as exc:
        raise HTTPException(status_code=422, detail=exc.key) from None


def report_filters(
    start_date: str | None = None,
    end_date: str | None = None,
    vehicle_id: str | None = None,
    vehicle_type: str | None = None,
) -> ReportFilters:
    """Optional report filters; dates are ISO (YYYY-MM-DD), vehicle_type is a group (trailer = both kinds)."""
    return _parse(
        {"start_date": start_date, "end_date": end_date, "vehicle_id": vehicle_id, "vehicle_type": vehicle_type}
    )


def maintenance_filters(
    start_date: str | None = None,
    end_date: str | None = None,
    vehicle_id: str | None = None,
    vehicle_type: str | None = None,
    status: str | None = None,
) -> MaintenanceReportFilters:
    """Report filters plus a maintenance status; dates apply to the service date (completed, else scheduled)."""
    return _parse(
        {
            "start_date": start_date,
            "end_date": end_date,
            "vehicle_id": vehicle_id,
            "vehicle_type": vehicle_type,
            "status": status,
        },
        MaintenanceReportFilters,
    )


def expense_filters(
    start_date: str | None = None,
    end_date: str | None = None,
    vehicle_id: str | None = None,
    vehicle_type: str | None = None,
    category: str | None = None,
) -> ExpenseReportFilters:
    """Report filters plus an expense category (fuel, toll, ...)."""
    return _parse(
        {
            "start_date": start_date,
            "end_date": end_date,
            "vehicle_id": vehicle_id,
            "vehicle_type": vehicle_type,
            "category": category,
        },
        ExpenseReportFilters,
    )


@router.get("/tco", response_model=list[TCOReportRow])
async def tco_report(
    filters: ReportFilters = Depends(report_filters),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    svc = ReportService(db)
    return await svc.tco_report(filters)


@router.get("/fleet-utilization")
async def fleet_utilization(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    svc = ReportService(db)
    return await svc.fleet_utilization()


@router.get("/fuel-consumption", response_model=list[FuelReportRow])
async def fuel_consumption(
    filters: ReportFilters = Depends(report_filters),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    svc = ReportService(db)
    return await svc.fuel_consumption(filters)


@router.get("/expense-analysis", response_model=ExpenseAnalysis)
async def expense_analysis(
    filters: ExpenseReportFilters = Depends(expense_filters),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    svc = ReportService(db)
    return await svc.expense_analysis(filters)


@router.get("/maintenance-history", response_model=list[MaintenanceReportRow])
async def maintenance_history(
    filters: MaintenanceReportFilters = Depends(maintenance_filters),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    svc = ReportService(db)
    return await svc.maintenance_history(filters)


# --- Export endpoints ---


@router.get("/export/tco.xlsx")
async def export_tco_excel(
    request: Request,
    filters: ReportFilters = Depends(report_filters),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_fleet_manager),
):
    svc = ReportService(db)
    data = await svc.export_tco_excel(filters, locale=getattr(request.state, "locale", "bg"))
    await AuditService(db).log_export(
        user_id=user.id,
        report=REPORT_NAMES[request.url.path.rsplit("/", 1)[-1]][0],
        file_format=REPORT_NAMES[request.url.path.rsplit("/", 1)[-1]][1],
        filters=filters.to_query(),
    )
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=tco_report.xlsx"},
    )


@router.get("/export/fuel.xlsx")
async def export_fuel_excel(
    request: Request,
    filters: ReportFilters = Depends(report_filters),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_fleet_manager),
):
    svc = ReportService(db)
    data = await svc.export_fuel_excel(filters, locale=getattr(request.state, "locale", "bg"))
    await AuditService(db).log_export(
        user_id=user.id,
        report=REPORT_NAMES[request.url.path.rsplit("/", 1)[-1]][0],
        file_format=REPORT_NAMES[request.url.path.rsplit("/", 1)[-1]][1],
        filters=filters.to_query(),
    )
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=fuel_consumption.xlsx"},
    )


@router.get("/export/maintenance.xlsx")
async def export_maintenance_excel(
    request: Request,
    filters: MaintenanceReportFilters = Depends(maintenance_filters),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_fleet_manager),
):
    svc = ReportService(db)
    data = await svc.export_maintenance_excel(filters, locale=getattr(request.state, "locale", "bg"))
    await AuditService(db).log_export(
        user_id=user.id,
        report=REPORT_NAMES[request.url.path.rsplit("/", 1)[-1]][0],
        file_format=REPORT_NAMES[request.url.path.rsplit("/", 1)[-1]][1],
        filters=filters.to_query(),
    )
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=maintenance_history.xlsx"},
    )


@router.get("/export/expenses.csv")
async def export_expenses_csv(
    request: Request,
    filters: ExpenseReportFilters = Depends(expense_filters),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_fleet_manager),
):
    svc = ReportService(db)
    data = await svc.export_expense_csv(filters, locale=getattr(request.state, "locale", "bg"))
    await AuditService(db).log_export(
        user_id=user.id,
        report=REPORT_NAMES[request.url.path.rsplit("/", 1)[-1]][0],
        file_format=REPORT_NAMES[request.url.path.rsplit("/", 1)[-1]][1],
        filters=filters.to_query(),
    )
    return Response(
        content=data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=expense_analysis.csv"},
    )
