from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.expense import ExpenseCategory
from app.models.user import User, UserRole
from app.models.vehicle import Vehicle
from app.repositories.base import BaseRepository
from app.schemas.expense import ExpenseCreate, ExpenseUpdate
from app.services.document_expense_sync import DOCUMENT_CATEGORIES, is_document_category
from app.services.errors import DomainError, MileageBelowCurrentError, render_error
from app.services.expense_service import ExpenseService
from app.services.validity import local_today
from app.services.vehicle_service import VehicleService
from app.vehicle_rules import LIST_TYPE_FILTERS, RETIRED_EXPENSE_CATEGORIES, vehicle_type_filter
from app.web.deps import can_contribute, driver_scope_of, get_web_user, vehicle_choice_for, web_require_roles
from app.web.pickers import option_rows, picker_state

router = APIRouter(prefix="/expenses", tags=["web-expenses"])
require_manager = web_require_roles(UserRole.ADMIN, UserRole.FLEET_MANAGER)
EDIT_FIELDS = (
    "category",
    "amount",
    "date",
    "vendor",
    "fuel_liters",
    "fuel_price_per_liter",
    "mileage_at_refuel",
    "description",
)
GENERAL_CATEGORIES = [
    c for c in ExpenseCategory if c.value not in DOCUMENT_CATEGORIES and c.value not in RETIRED_EXPENSE_CATEGORIES
]
LISTED_CATEGORIES = [c for c in ExpenseCategory if c.value not in RETIRED_EXPENSE_CATEGORIES]
DOCUMENT_CATEGORY_LIST = [c for c in ExpenseCategory if c.value in DOCUMENT_CATEGORIES]


def _filter_date(value: str | None) -> date | None:
    """A date filter from the query string; empty or invalid values mean no limit."""
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _ctx(request: Request, user: User, **values) -> dict:
    return {
        "request": request,
        "user": user,
        "active_page": "expenses",
        "can_edit": user.role in (UserRole.ADMIN, UserRole.FLEET_MANAGER),
        # an assigned driver adds expenses and uploads receipts for their own vehicle only
        "can_add": can_contribute(request, user),
        "can_upload": can_contribute(request, user),
        **values,
        **request.app.state.template_globals(request),
    }


DECIMAL_FIELDS = ("amount", "fuel_liters")


def _decimal_text(value: str) -> str:
    """Accept a decimal comma (``80,5``) as typed on a Bulgarian keyboard; ``1,234.5`` is left alone."""
    value = value.strip()
    return value.replace(",", ".") if "," in value and "." not in value else value


def _submitted(form) -> dict:
    values = {k: (v if v != "" else None) for k, v in form.items() if isinstance(v, str)}
    for name in DECIMAL_FIELDS:
        if values.get(name):
            values[name] = _decimal_text(values[name])
    return values


async def _render_form(
    request: Request,
    db: AsyncSession,
    user: User,
    *,
    form_data: dict,
    expense=None,
    error: str | None = None,
    mileage_warning: MileageBelowCurrentError | None = None,
):
    vehicle = expense.vehicle if expense else None
    if vehicle is None and form_data.get("vehicle_id"):
        try:
            vehicle = await db.get(Vehicle, UUID(str(form_data["vehicle_id"])))
        except ValueError:
            vehicle = None
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="expenses/form.html",
        status_code=409 if mileage_warning else 400 if error else 200,
        context=_ctx(
            request,
            user,
            expense=expense,
            action=f"/expenses/{expense.id}/edit" if expense else "/expenses/new",
            form_data=form_data,
            picker=picker_state(vehicle),
            general_categories=GENERAL_CATEGORIES,
            # An existing expense in a retired category (e.g. generic insurance) can still be edited.
            legacy_category=expense.category
            if expense and expense.category.value in RETIRED_EXPENSE_CATEGORIES
            else None,
            document_categories=[] if driver_scope_of(request).restricted else DOCUMENT_CATEGORY_LIST,
            document_values=sorted(DOCUMENT_CATEGORIES),
            error=error,
            mileage_warning=mileage_warning,
        ),
    )


async def _discard_failed_write(db: AsyncSession, user: User, exc: Exception) -> None:
    """Only a database error leaves a failed transaction behind; validation errors wrote nothing."""
    if isinstance(exc, IntegrityError):
        await db.rollback()
        await db.refresh(user)


def _error_text(exc: Exception, request: Request) -> str:
    if isinstance(exc, (ValidationError, IntegrityError)):
        key = (
            next((d.get("ctx", {}).get("error") for d in exc.errors()), None)
            if isinstance(exc, ValidationError)
            else None
        )
        return render_error(key or "errors.invalid_input", request.state.locale)
    return render_error(exc, request.state.locale)


@router.get("", response_class=HTMLResponse)
async def expense_list(
    request: Request,
    category: str | None = None,
    q: str | None = None,
    vehicle_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    start, end = _filter_date(date_from), _filter_date(date_to)
    if start and end and start > end:
        start, end = end, start  # a reversed range is read as meant, not as an empty list
    try:
        category_enum = ExpenseCategory(category) if category else None
    except ValueError:
        category_enum = None
    try:
        types = vehicle_type_filter(vehicle_type)
    except ValueError:
        types, vehicle_type = None, None
    items, total = await ExpenseService(db).list_all(
        category=category_enum,
        q=q,
        vehicle_types=types,
        vehicle_ids=driver_scope_of(request).vehicle_filter,
        date_from=start,
        date_to=end,
        page=page,
        size=size,
    )
    partial = request.headers.get("HX-Request") == "true" and request.headers.get("HX-Target") == "expense-results"
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="expenses/partials/list_results.html" if partial else "expenses/list.html",
        context=_ctx(
            request,
            user,
            expenses=items,
            total=total,
            page=page,
            size=size,
            pages=BaseRepository.calc_pages(total, size),
            q=q or "",
            type_filter=vehicle_type or "",
            type_filters=LIST_TYPE_FILTERS,
            category_filter=category_enum,
            categories=LISTED_CATEGORIES,
            date_from=start.isoformat() if start else "",
            date_to=end.isoformat() if end else "",
        ),
    )


@router.get("/vehicle-options", response_class=HTMLResponse)
async def vehicle_options(request: Request, q: str | None = None, db: AsyncSession = Depends(get_db)):
    """HTMX partial for the searchable vehicle picker."""
    user = await get_web_user(request, db)
    if not user:
        return HTMLResponse("", status_code=401)
    vehicles = await VehicleService(db).picker_options(q, vehicle_ids=driver_scope_of(request).vehicle_filter)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="components/vehicle_picker_options.html",
        context=_ctx(request, user, options=option_rows(vehicles)),
    )


@router.get("/new", response_class=HTMLResponse)
async def expense_form_new(
    request: Request,
    vehicle_id: str | None = None,
    category: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if not can_contribute(request, user):
        return RedirectResponse(url="/", status_code=302)
    vehicle_id = vehicle_choice_for(request, vehicle_id)
    form_data = {
        "vehicle_id": vehicle_id,
        "category": category if category in {c.value for c in ExpenseCategory} else "other",
        "date": local_today().isoformat(),
    }
    return await _render_form(request, db, user, form_data=form_data)


@router.post("/new", response_class=HTMLResponse)
async def expense_create(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if not can_contribute(request, user):
        return RedirectResponse(url="/", status_code=302)
    scope = driver_scope_of(request)
    raw = _submitted(await request.form())
    try:
        if not raw.get("vehicle_id"):
            raise DomainError("error.expense_vehicle_required")
        category = raw.get("category") or "other"
        if scope.restricted:  # a driver adds expenses for their own vehicle, never insurance / inspection documents
            if vehicle_choice_for(request, raw["vehicle_id"]) != raw["vehicle_id"]:
                raise DomainError("errors.forbidden")
            if is_document_category(category):
                raise DomainError("error.expense_driver_category")
        if is_document_category(category) and not (raw.get("valid_from") and raw.get("valid_until")):
            raise DomainError("error.expense_document_dates_required")
        data = ExpenseCreate(
            vehicle_id=raw.get("vehicle_id"),
            driver_id=scope.driver_id if scope.restricted else None,
            category=category,
            amount=raw.get("amount") or 0,
            currency=raw.get("currency", "EUR"),
            date=raw.get("date"),
            description=raw.get("description"),
            vendor=raw.get("vendor"),
            fuel_liters=raw.get("fuel_liters") if category == "fuel" else None,
            mileage_at_refuel=raw.get("mileage_at_refuel"),
            # The validity fields are hidden for other categories; ignore anything left in them.
            valid_from=raw.get("valid_from") if is_document_category(category) else None,
            valid_until=raw.get("valid_until") if is_document_category(category) else None,
            policy_number=raw.get("policy_number") if is_document_category(category) else None,
            overwrite_mileage=raw.get("confirm_mileage_overwrite") == "1",
        )
        expense = await ExpenseService(db).create(data, user.id)
        await db.commit()
    except MileageBelowCurrentError as exc:
        return await _render_form(request, db, user, form_data=raw, mileage_warning=exc)
    except (ValidationError, DomainError, IntegrityError) as exc:
        await _discard_failed_write(db, user, exc)
        return await _render_form(request, db, user, form_data=raw, error=_error_text(exc, request))
    return RedirectResponse(url=f"/expenses/{expense.id}", status_code=303)


@router.get("/{expense_id}", response_class=HTMLResponse)
async def expense_detail(request: Request, expense_id: UUID, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    expense = await ExpenseService(db).get_by_id(expense_id)
    if not expense or not driver_scope_of(request).allows_vehicle(expense.vehicle_id):
        return RedirectResponse(url="/expenses", status_code=302)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="expenses/detail.html",
        context=_ctx(request, user, expense=expense),
    )


@router.get("/{expense_id}/edit", response_class=HTMLResponse)
async def expense_edit(
    request: Request, expense_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)
):
    expense = await ExpenseService(db).get_by_id(expense_id)
    if not expense:
        return RedirectResponse(url="/expenses", status_code=302)
    form_data = {name: getattr(expense, name) for name in EDIT_FIELDS}
    form_data.update(category=expense.category.value, vehicle_id=str(expense.vehicle_id))
    return await _render_form(request, db, user, form_data=form_data, expense=expense)


@router.post("/{expense_id}/edit", response_class=HTMLResponse)
async def expense_update(
    request: Request, expense_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)
):
    service = ExpenseService(db)
    expense = await service.get_by_id(expense_id)
    if not expense:
        return RedirectResponse(url="/expenses", status_code=302)
    raw = _submitted(await request.form())
    linked = expense.compliance_document is not None
    try:
        values = {
            name: raw.get(name)
            for name in EDIT_FIELDS
            if name != "fuel_price_per_liter" and (name != "category" or (raw.get(name) and not linked))
        }
        if ("category" in values and values["category"] != "fuel") or not values.get("fuel_liters"):
            values["fuel_liters"] = values["fuel_price_per_liter"] = None  # no liters, no price per liter
        data = ExpenseUpdate(**values, overwrite_mileage=raw.get("confirm_mileage_overwrite") == "1")
        await service.update(expense_id, data)
        await db.commit()
    except MileageBelowCurrentError as exc:
        raw.setdefault("category", expense.category.value)
        raw["vehicle_id"] = str(expense.vehicle_id)
        return await _render_form(request, db, user, form_data=raw, expense=expense, mileage_warning=exc)
    except (ValidationError, DomainError, IntegrityError) as exc:
        await _discard_failed_write(db, user, exc)
        expense = await service.get_by_id(expense_id)
        raw.setdefault("category", expense.category.value)
        raw["vehicle_id"] = str(expense.vehicle_id)
        return await _render_form(request, db, user, form_data=raw, expense=expense, error=_error_text(exc, request))
    return RedirectResponse(url=f"/expenses/{expense_id}", status_code=303)


@router.post("/{expense_id}/delete")
async def expense_delete(
    request: Request, expense_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)
):
    try:
        await ExpenseService(db).delete(expense_id)
    except DomainError:
        pass
    return RedirectResponse(url="/expenses", status_code=303)
