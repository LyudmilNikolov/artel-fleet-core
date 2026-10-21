from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.expense import Currency, ExpenseCategory
from app.models.vehicle import Vehicle
from app.repositories.base import BaseRepository
from app.schemas.expense import ExpenseCreate
from app.services.errors import render_error
from app.services.expense_service import ExpenseService
from app.web.deps import get_web_user

router = APIRouter(prefix="/expenses", tags=["web-expenses"])


@router.get("", response_class=HTMLResponse)
async def expense_list(
    request: Request,
    category: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    category_enum = ExpenseCategory(category) if category else None
    service = ExpenseService(db)
    items, total = await service.list_all(category=category_enum, page=page, size=size)
    pages = BaseRepository.calc_pages(total, size)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="expenses/list.html",
        context={
            "request": request,
            "user": user,
            "active_page": "expenses",
            "expenses": items,
            "total": total,
            "page": page,
            "size": size,
            "pages": pages,
            "category_filter": category_enum,
            "categories": ExpenseCategory,
            **request.app.state.template_globals(request),
        },
    )


@router.get("/new", response_class=HTMLResponse)
async def expense_form_new(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="expenses/form.html",
        context={
            "request": request,
            "user": user,
            "active_page": "expenses",
            "expense": None,
            "vehicles": (await db.scalars(select(Vehicle).order_by(Vehicle.license_plate))).all(),
            "categories": ExpenseCategory,
            "currencies": Currency,
            "error": None,
            **request.app.state.template_globals(request),
        },
    )


@router.post("/new", response_class=HTMLResponse)
async def expense_create(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    form = await request.form()
    try:
        data = ExpenseCreate(
            vehicle_id=form.get("vehicle_id"),
            category=form.get("category", "other"),
            amount=form.get("amount", 0),
            currency=form.get("currency", "EUR"),
            date=form.get("date"),
            description=form.get("description") or None,
            vendor=form.get("vendor") or None,
            fuel_liters=float(form.get("fuel_liters")) if form.get("fuel_liters") else None,
            fuel_price_per_liter=form.get("fuel_price_per_liter") or None,
        )
        if await db.get(Vehicle, data.vehicle_id) is None:
            raise ValueError("errors.invalid_input")
        service = ExpenseService(db)
        await service.create(data, user.id)
        await db.commit()
        return RedirectResponse(url="/expenses", status_code=303)
    except (ValueError, IntegrityError) as e:
        await db.rollback()
        await db.refresh(user)
        error = render_error(
            ValueError("errors.invalid_input") if isinstance(e, IntegrityError) else e, request.state.locale
        )
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="expenses/form.html",
            context={
                "request": request,
                "user": user,
                "active_page": "expenses",
                "expense": None,
                "vehicles": (await db.scalars(select(Vehicle).order_by(Vehicle.license_plate))).all(),
                "categories": ExpenseCategory,
                "currencies": Currency,
                "error": error,
                **request.app.state.template_globals(request),
            },
            status_code=400,
        )
