"""Web pages for importing toll transactions (Shell Priced Transaction CSV) as expenses.

Upload -> preview (nothing written) -> import or cancel. Admins and fleet managers only.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.toll_import import TollImportStatus
from app.models.user import User, UserRole
from app.repositories.base import BaseRepository
from app.services.errors import DomainError, render_error
from app.services.toll_import_service import TollImportService
from app.utils.s3 import MAX_FILE_SIZE
from app.utils.upload_validation import read_limited, sanitize_filename
from app.web.deps import web_require_roles

router = APIRouter(prefix="/expenses/imports", tags=["web-toll-imports"])
require_manager = web_require_roles(UserRole.ADMIN, UserRole.FLEET_MANAGER)


def _render(request: Request, user: User, name: str, status_code: int = 200, **values):
    return request.app.state.templates.TemplateResponse(
        request=request,
        name=name,
        status_code=status_code,
        context={
            "request": request,
            "user": user,
            "active_page": "expenses",
            **values,
            **request.app.state.template_globals(request),
        },
    )


async def _index(request: Request, db: AsyncSession, user: User, page: int = 1, error: str | None = None):
    service = TollImportService(db)
    batches, total = await service.list_batches(page=page)
    return _render(
        request,
        user,
        "expenses/imports/index.html",
        status_code=400 if error else 200,
        batches=batches,
        waiting=await service.waiting(),
        page=page,
        pages=BaseRepository.calc_pages(total, 20),
        total=total,
        error=error,
    )


@router.get("", response_class=HTMLResponse)
async def import_index(
    request: Request,
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
):
    return await _index(request, db, user, page)


@router.post("", response_class=HTMLResponse)
async def import_upload(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
):
    try:
        data = await read_limited(file, MAX_FILE_SIZE)
        if not data:
            raise DomainError("error.toll_import_empty")
        batch = await TollImportService(db).preview(data, sanitize_filename(file.filename, "import.csv"), user)
        await db.commit()
    except DomainError as exc:
        await db.rollback()
        await db.refresh(user)
        return await _index(request, db, user, error=render_error(exc, request.state.locale))
    return RedirectResponse(url=f"/expenses/imports/{batch.id}", status_code=303)


@router.get("/{batch_id}", response_class=HTMLResponse)
async def import_detail(
    request: Request,
    batch_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
):
    service = TollImportService(db)
    batch = await service.get(batch_id)
    if batch is None:
        return RedirectResponse(url="/expenses/imports", status_code=302)
    applied = batch.status == TollImportStatus.APPLIED
    imported = await service.imported_ids(p.get("transaction_id") for p in batch.problems) if applied else set()
    return _render(
        request,
        user,
        "expenses/imports/detail.html",
        batch=batch,
        applied=applied,
        resolved=imported,
        error=request.query_params.get("error") and render_error(request.query_params["error"], request.state.locale),
    )


@router.post("/{batch_id}/apply")
async def import_apply(
    request: Request,
    batch_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
):
    try:
        await TollImportService(db).apply(batch_id, user)
        await db.commit()
    except DomainError as exc:
        await db.rollback()
        return RedirectResponse(url=f"/expenses/imports/{batch_id}?error={exc.key}", status_code=303)
    return RedirectResponse(url=f"/expenses/imports/{batch_id}", status_code=303)


@router.post("/{batch_id}/cancel")
async def import_cancel(
    request: Request,
    batch_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
):
    await TollImportService(db).cancel(batch_id)
    await db.commit()
    return RedirectResponse(url="/expenses/imports", status_code=303)
