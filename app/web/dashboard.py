from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.dashboard_service import DashboardService
from app.web.deps import get_web_user

router = APIRouter(tags=["web-dashboard"])


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="dashboard/index.html",
        context={
            "request": request,
            "user": user,
            "active_page": "dashboard",
            **request.app.state.template_globals(request),
        },
    )


@router.get("/widgets/fleet-overview", response_class=HTMLResponse)
async def widget_fleet_overview(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return HTMLResponse("")
    svc = DashboardService(db)
    data = await svc.fleet_overview()
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="dashboard/partials/fleet_overview.html",
        context={"request": request, "data": data, **request.app.state.template_globals(request)},
    )


@router.get("/widgets/attention-needed", response_class=HTMLResponse)
async def widget_attention_needed(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return HTMLResponse("")
    svc = DashboardService(db)
    data = await svc.attention_needed()
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="dashboard/partials/attention_needed.html",
        context={"request": request, "data": data, **request.app.state.template_globals(request)},
    )


@router.get("/widgets/expense-chart", response_class=HTMLResponse)
async def widget_expense_chart(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return HTMLResponse("")
    svc = DashboardService(db)
    data = await svc.expense_summary()
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="dashboard/partials/expense_chart.html",
        context={"request": request, "data": data, **request.app.state.template_globals(request)},
    )


@router.get("/widgets/maintenance-stats", response_class=HTMLResponse)
async def widget_maintenance_stats(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return HTMLResponse("")
    svc = DashboardService(db)
    stats = await svc.maintenance_stats()
    recent = await svc.recent_maintenance()
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="dashboard/partials/maintenance_stats.html",
        context={
            "request": request,
            "stats": stats,
            "recent": recent,
            **request.app.state.template_globals(request),
        },
    )


@router.get("/widgets/top-vehicles", response_class=HTMLResponse)
async def widget_top_vehicles(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return HTMLResponse("")
    svc = DashboardService(db)
    vehicles = await svc.top_expensive_vehicles()
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="dashboard/partials/top_vehicles.html",
        context={
            "request": request,
            "vehicles": vehicles,
            **request.app.state.template_globals(request),
        },
    )
