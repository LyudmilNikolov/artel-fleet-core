import datetime as dt

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.report_service import ReportService
from app.web.deps import get_web_user

router = APIRouter(prefix="/reports", tags=["web-reports"])


def _parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    return dt.date.fromisoformat(value)


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


@router.get("/tco", response_class=HTMLResponse)
async def report_tco(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    sd = _parse_date(start_date)
    ed = _parse_date(end_date)
    svc = ReportService(db)
    data = await svc.tco_report(sd, ed)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="reports/tco.html",
        context={
            "request": request,
            "user": user,
            "active_page": "reports",
            "data": data,
            "start_date": sd,
            "end_date": ed,
            **request.app.state.template_globals(request),
        },
    )


@router.get("/fuel", response_class=HTMLResponse)
async def report_fuel(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    sd = _parse_date(start_date)
    ed = _parse_date(end_date)
    svc = ReportService(db)
    data = await svc.fuel_consumption(sd, ed)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="reports/fuel.html",
        context={
            "request": request,
            "user": user,
            "active_page": "reports",
            "data": data,
            "start_date": sd,
            "end_date": ed,
            **request.app.state.template_globals(request),
        },
    )


@router.get("/expenses", response_class=HTMLResponse)
async def report_expenses(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    sd = _parse_date(start_date)
    ed = _parse_date(end_date)
    svc = ReportService(db)
    data = await svc.expense_analysis(sd, ed)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="reports/expenses.html",
        context={
            "request": request,
            "user": user,
            "active_page": "reports",
            "data": data,
            "start_date": sd,
            "end_date": ed,
            **request.app.state.template_globals(request),
        },
    )


# Excel/CSV exports from web UI


@router.get("/export/tco.xlsx")
async def web_export_tco(
    start_date: str | None = None,
    end_date: str | None = None,
    request: Request = None,
    db: AsyncSession = Depends(get_db),
):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    svc = ReportService(db)
    content = await svc.export_tco_excel(_parse_date(start_date), _parse_date(end_date), locale=request.state.locale)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=tco_report.xlsx"},
    )


@router.get("/export/fuel.xlsx")
async def web_export_fuel(
    start_date: str | None = None,
    end_date: str | None = None,
    request: Request = None,
    db: AsyncSession = Depends(get_db),
):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    svc = ReportService(db)
    content = await svc.export_fuel_excel(_parse_date(start_date), _parse_date(end_date), locale=request.state.locale)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=fuel_consumption.xlsx"},
    )


@router.get("/export/expenses.csv")
async def web_export_expenses(
    start_date: str | None = None,
    end_date: str | None = None,
    request: Request = None,
    db: AsyncSession = Depends(get_db),
):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    svc = ReportService(db)
    content = await svc.export_expense_csv(_parse_date(start_date), _parse_date(end_date), locale=request.state.locale)
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=expense_analysis.csv"},
    )
