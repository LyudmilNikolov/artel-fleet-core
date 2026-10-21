from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.contract import ContractStatus, ContractType, PaymentFrequency
from app.repositories.base import BaseRepository
from app.schemas.contract import ContractCreate
from app.services.contract_service import ContractService
from app.services.errors import render_error
from app.web.deps import get_web_user

router = APIRouter(prefix="/contracts", tags=["web-contracts"])


@router.get("", response_class=HTMLResponse)
async def contract_list(
    request: Request,
    status: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    status_enum = ContractStatus(status) if status else None
    service = ContractService(db)
    items, total = await service.list_all(status=status_enum, page=page, size=size)
    pages = BaseRepository.calc_pages(total, size)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="contracts/list.html",
        context={
            "request": request,
            "user": user,
            "active_page": "contracts",
            "contracts": items,
            "total": total,
            "page": page,
            "size": size,
            "pages": pages,
            "status_filter": status_enum,
            "statuses": ContractStatus,
            **request.app.state.template_globals(request),
        },
    )


@router.get("/new", response_class=HTMLResponse)
async def contract_form_new(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="contracts/form.html",
        context={
            "request": request,
            "user": user,
            "active_page": "contracts",
            "contract": None,
            "types": ContractType,
            "statuses": ContractStatus,
            "frequencies": PaymentFrequency,
            "error": None,
            **request.app.state.template_globals(request),
        },
    )


@router.post("/new", response_class=HTMLResponse)
async def contract_create(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    form = await request.form()
    try:
        data = ContractCreate(
            vehicle_id=form.get("vehicle_id"),
            type=form.get("type", "leasing"),
            contractor=form.get("contractor", ""),
            contract_number=form.get("contract_number") or None,
            start_date=form.get("start_date"),
            end_date=form.get("end_date"),
            amount=form.get("amount") or None,
            payment_frequency=form.get("payment_frequency", "one_time"),
            auto_renew=form.get("auto_renew") == "on",
            notes=form.get("notes") or None,
        )
        service = ContractService(db)
        await service.create(data, user.id)
        return RedirectResponse(url="/contracts", status_code=302)
    except (ValueError, Exception) as e:
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="contracts/form.html",
            context={
                "request": request,
                "user": user,
                "active_page": "contracts",
                "contract": None,
                "types": ContractType,
                "statuses": ContractStatus,
                "frequencies": PaymentFrequency,
                "error": render_error(e, request.state.locale),
                **request.app.state.template_globals(request),
            },
        )


@router.get("/{contract_id}", response_class=HTMLResponse)
async def contract_detail(request: Request, contract_id: UUID, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    service = ContractService(db)
    contract = await service.get_by_id(contract_id)
    if not contract:
        return RedirectResponse(url="/contracts", status_code=302)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="contracts/detail.html",
        context={
            "request": request,
            "user": user,
            "active_page": "contracts",
            "contract": contract,
            **request.app.state.template_globals(request),
        },
    )
