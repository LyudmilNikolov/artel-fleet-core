from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_fleet_manager
from app.database import get_db
from app.models.document import DocumentType, EntityType
from app.models.user import User
from app.services.document_service import DocumentService
from app.services.driver_access import can_view_scans
from app.services.errors import error_detail
from app.utils.s3 import MAX_FILE_SIZE
from app.utils.upload_validation import read_limited

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    entity_type: EntityType = Form(...),
    entity_id: UUID = Form(...),
    doc_type: DocumentType = Form(DocumentType.OTHER),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_fleet_manager),
):
    service = DocumentService(db)
    try:
        file_data = await read_limited(file, MAX_FILE_SIZE)  # stops reading above the size limit
        doc = await service.upload(
            file_data=file_data,
            filename=file.filename or "unknown",
            mime_type=file.content_type or "application/octet-stream",
            entity_type=entity_type,
            entity_id=entity_id,
            doc_type=doc_type,
            uploaded_by=user.id,
        )
        return {
            "id": str(doc.id),
            "filename": doc.filename,
            "mime_type": doc.mime_type,
            "size_bytes": doc.size_bytes,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=error_detail(e))


@router.get("/{doc_id}/download")
async def download_document(
    doc_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    service = DocumentService(db)
    doc = await service.get(doc_id)
    if doc and not await can_view_scans(db, user, doc.entity_type, doc.entity_id):
        raise HTTPException(status_code=403, detail="errors.forbidden")
    url = await service.get_download_url(doc_id)
    if not url:
        raise HTTPException(status_code=404, detail="error.document_not_found")
    return RedirectResponse(url=url)


@router.get("/entity/{entity_type}/{entity_id}")
async def list_entity_documents(
    entity_type: EntityType,
    entity_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not await can_view_scans(db, user, entity_type, entity_id):
        raise HTTPException(status_code=403, detail="errors.forbidden")
    service = DocumentService(db)
    docs = await service.list_for_entity(entity_type, entity_id)
    return [
        {
            "id": str(d.id),
            "filename": d.filename,
            "mime_type": d.mime_type,
            "size_bytes": d.size_bytes,
            "type": d.type.value,
            "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
        }
        for d in docs
    ]


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    doc_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_fleet_manager),
):
    service = DocumentService(db)
    try:
        await service.delete(doc_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=error_detail(e))
