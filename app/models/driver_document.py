"""Per-driver tracked documents (ID card, licence, tachograph card, ...) with an optional validity period."""

import uuid
from datetime import date

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base, TimestampMixin, UUIDPrimaryKey


class DriverDocument(Base, UUIDPrimaryKey, TimestampMixin):
    """One validity period of one document type for one driver.

    Status (valid / expiring soon / expired / no expiry) is computed from ``end_date`` and never stored.
    A NULL ``end_date`` means the document does not expire. Per-type rules live in
    ``app.driver_document_rules`` and are enforced by the service.
    """

    __tablename__ = "driver_documents"
    __table_args__ = (
        CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR end_date >= start_date",
            name="driver_documents_period_order",
        ),
        Index("ix_driver_documents_current", "driver_id", "document_type", "end_date"),
    )

    driver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id", ondelete="CASCADE"), nullable=False
    )
    document_type: Mapped[str] = mapped_column(String(40), nullable=False)
    document_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    categories: Mapped[str | None] = mapped_column(String(50), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    renewed_from_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("driver_documents.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    driver = relationship("Driver", foreign_keys=[driver_id], back_populates="documents", lazy="raise")
    creator = relationship("User", foreign_keys=[created_by], lazy="raise")
