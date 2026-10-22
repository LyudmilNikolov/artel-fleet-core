"""Web routes for document upload, download, and management."""

from html import escape
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.document import DocumentType, EntityType
from app.services.document_service import DocumentService
from app.services.driver_access import can_upload_scans, can_view_scans
from app.services.errors import render_error
from app.web.deps import get_web_user

router = APIRouter(prefix="/documents", tags=["web-documents"])

# Map entity_type string to return URL
ENTITY_RETURN_URLS = {
    "vehicle": "/vehicles/{id}",
    "driver": "/drivers/{id}",
    "driver_document": "/driver-documents/{id}",
    "maintenance": "/maintenance/{id}",
    "compliance_document": "/compliance/{id}",
    "expense": "/expenses/{id}",
}


@router.post("/upload", response_class=HTMLResponse)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    entity_type: str = Form(...),
    entity_id: str = Form(...),
    doc_type: str = Form("other"),
    db: AsyncSession = Depends(get_db),
):
    """Upload a document and return updated gallery partial via HTMX."""
    user = await get_web_user(request, db)
    if not user:
        return HTMLResponse(
            escape(render_error(ValueError("errors.unauthorized"), request.state.locale)), status_code=401
        )

    try:
        etype = EntityType(entity_type)
        dtype = DocumentType(doc_type)
        eid = UUID(entity_id)
    except (ValueError, KeyError):
        return HTMLResponse(
            escape(render_error(ValueError("errors.invalid_input"), request.state.locale)), status_code=400
        )
    # Managers upload anywhere; a driver only to their own profile and their own document entries.
    if not await can_upload_scans(db, user, etype, eid):
        return HTMLResponse(escape(render_error(ValueError("errors.forbidden"), request.state.locale)), status_code=403)

    file_data = await file.read()
    service = DocumentService(db)
    try:
        await service.upload(
            file_data=file_data,
            filename=file.filename or "upload",
            mime_type=file.content_type or "application/octet-stream",
            entity_type=etype,
            entity_id=eid,
            doc_type=dtype,
            uploaded_by=user.id,
        )
        await db.commit()
    except ValueError as e:
        return HTMLResponse(escape(render_error(e, request.state.locale)), status_code=400)

    # Return updated document gallery via HTMX
    documents = await service.list_for_entity(etype, eid)
    for doc in documents:
        doc._download_url = await service.get_download_url(doc.id)
        doc._preview_url = doc._download_url if doc.mime_type and doc.mime_type.startswith("image/") else None
    templates = request.app.state.templates
    ctx = request.app.state.template_globals(request)
    return templates.TemplateResponse(
        request=request,
        name="components/document_gallery.html",
        context={
            "request": request,
            "documents": documents,
            "entity_type": entity_type,
            "entity_id": str(eid),
            "can_edit": user.role.value in ("admin", "fleet_manager"),
            "viewer_id": user.id,
            "can_delete_own": True,
            **ctx,
        },
    )


@router.get("/gallery/{entity_type}/{entity_id}", response_class=HTMLResponse)
async def document_gallery(
    request: Request,
    entity_type: str,
    entity_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Load document gallery partial for an entity (HTMX lazy-load)."""
    user = await get_web_user(request, db)
    if not user:
        return HTMLResponse("", status_code=401)

    try:
        etype = EntityType(entity_type)
    except ValueError:
        return HTMLResponse("", status_code=400)

    if not await can_view_scans(db, user, etype, entity_id):
        return HTMLResponse("", status_code=403)

    service = DocumentService(db)
    documents = await service.list_for_entity(etype, entity_id)

    # Generate presigned URLs for images
    for doc in documents:
        if doc.mime_type and doc.mime_type.startswith("image/"):
            doc._preview_url = await service.get_download_url(doc.id)
        else:
            doc._preview_url = None
        doc._download_url = await service.get_download_url(doc.id)

    templates = request.app.state.templates
    ctx = request.app.state.template_globals(request)
    return templates.TemplateResponse(
        request=request,
        name="components/document_gallery.html",
        context={
            "request": request,
            "documents": documents,
            "entity_type": entity_type,
            "entity_id": str(entity_id),
            "can_edit": user.role.value in ("admin", "fleet_manager"),
            "viewer_id": user.id,
            "can_delete_own": await can_upload_scans(db, user, etype, entity_id),
            **ctx,
        },
    )


@router.get("/{doc_id}/download")
async def download_document(
    request: Request,
    doc_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Redirect to presigned S3 URL for download."""
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    service = DocumentService(db)
    doc = await service.get(doc_id)
    if doc and not await can_view_scans(db, user, doc.entity_type, doc.entity_id):
        return RedirectResponse(url="/", status_code=302)
    url = await service.get_download_url(doc_id)
    if not url:
        return RedirectResponse(url="/", status_code=302)
    return RedirectResponse(url=url, status_code=302)


@router.delete("/{doc_id}", response_class=HTMLResponse)
async def delete_document(
    request: Request,
    doc_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Delete a document and return empty response for HTMX swap."""
    user = await get_web_user(request, db)
    if not user:
        return HTMLResponse("", status_code=403)

    service = DocumentService(db)
    if user.role.value not in ("admin", "fleet_manager"):
        # A driver may remove only a file they uploaded themselves to their own record.
        doc = await service.get(doc_id)
        if not (
            doc and doc.uploaded_by == user.id and await can_upload_scans(db, user, doc.entity_type, doc.entity_id)
        ):
            return HTMLResponse("", status_code=403)
    try:
        await service.delete(doc_id)
        await db.commit()
    except ValueError:
        pass

    return HTMLResponse("")
