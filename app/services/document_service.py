from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company_licence import CompanyLicence
from app.models.compliance import ComplianceDocument
from app.models.document import Document, DocumentType, EntityType
from app.models.driver_document import DriverDocument
from app.services.errors import DomainError
from app.utils.s3 import ALLOWED_MIME_TYPES, MAX_FILE_SIZE, get_s3_client
from app.utils.upload_validation import check_content, sanitize_filename


class DocumentService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def upload(
        self,
        *,
        file_data: bytes,
        filename: str,
        mime_type: str,
        entity_type: EntityType,
        entity_id: UUID,
        doc_type: DocumentType = DocumentType.OTHER,
        uploaded_by: UUID | None = None,
    ) -> Document:
        if entity_type == EntityType.COMPLIANCE_DOCUMENT and not await self.session.get(ComplianceDocument, entity_id):
            raise DomainError("error.compliance_not_found")
        if entity_type == EntityType.COMPANY_LICENCE and not await self.session.get(CompanyLicence, entity_id):
            raise DomainError("error.company_licence_not_found")
        if entity_type == EntityType.DRIVER_DOCUMENT and not await self.session.get(DriverDocument, entity_id):
            raise DomainError("error.driver_document_not_found")
        if mime_type not in ALLOWED_MIME_TYPES:
            raise DomainError("error.file_type", mime_type=mime_type)
        if len(file_data) > MAX_FILE_SIZE:
            raise DomainError("error.file_too_large")
        mime_type = check_content(mime_type, file_data)  # the content must really be what is claimed
        filename = sanitize_filename(filename)

        s3 = get_s3_client()
        folder = f"{entity_type.value}/{entity_id}"
        s3_key = s3.upload_file(file_data, filename, mime_type, folder=folder)

        doc = Document(
            entity_type=entity_type,
            entity_id=entity_id,
            type=doc_type,
            filename=filename,
            s3_key=s3_key,
            mime_type=mime_type,
            size_bytes=len(file_data),
            uploaded_by=uploaded_by,
        )
        self.session.add(doc)
        await self.session.flush()
        await self.session.refresh(doc)
        return doc

    async def get(self, doc_id: UUID) -> Document | None:
        """Return one document row, or None."""
        return await self.session.get(Document, doc_id)

    async def get_download_url(self, doc_id: UUID, attachment: bool = False) -> str | None:
        """A short-lived storage link; ``attachment`` makes the browser save the file instead of showing it."""
        doc = await self.session.get(Document, doc_id)
        if not doc:
            return None
        s3 = get_s3_client()
        extra = {"attachment": True} if attachment else {}
        return s3.get_presigned_url(doc.s3_key, filename=doc.filename, mime_type=doc.mime_type, **extra)

    async def list_for_entity(self, entity_type: EntityType, entity_id: UUID) -> list[Document]:
        result = await self.session.execute(
            select(Document)
            .where(Document.entity_type == entity_type, Document.entity_id == entity_id)
            .order_by(Document.uploaded_at.desc())
        )
        return list(result.scalars().all())

    async def delete(self, doc_id: UUID) -> None:
        doc = await self.session.get(Document, doc_id)
        if not doc:
            raise DomainError("error.document_not_found")
        s3 = get_s3_client()
        s3.delete_file(doc.s3_key)
        await self.session.delete(doc)
        await self.session.flush()
