import enum
import uuid

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base, UUIDPrimaryKey


class EntityType(str, enum.Enum):
    VEHICLE = "vehicle"
    DRIVER = "driver"
    DRIVER_DOCUMENT = "driver_document"
    MAINTENANCE = "maintenance"
    COMPLIANCE_DOCUMENT = "compliance_document"
    EXPENSE = "expense"


class DocumentType(str, enum.Enum):
    PHOTO = "photo"
    SCAN = "scan"
    INVOICE = "invoice"
    ACT = "act"
    CONTRACT = "contract"
    LICENSE = "license"
    MEDICAL = "medical"
    INSURANCE = "insurance"
    OTHER = "other"


class Document(Base, UUIDPrimaryKey):
    __tablename__ = "documents"

    entity_type: Mapped[EntityType] = mapped_column(Enum(EntityType, name="entity_type"), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    type: Mapped[DocumentType] = mapped_column(
        Enum(DocumentType, name="document_type"), default=DocumentType.OTHER, nullable=False
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    s3_key: Mapped[str] = mapped_column(String(1000), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    uploaded_at: Mapped[str] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    uploader = relationship("User", foreign_keys=[uploaded_by])
