"""Per-vehicle compliance documents (Документи и срокове) with a validity period."""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base, TimestampMixin, UUIDPrimaryKey


class ComplianceDocument(Base, UUIDPrimaryKey, TimestampMixin):
    """One validity period of one compliance document type for one vehicle.

    Status (valid / expiring soon / expired) is computed from ``end_date`` and never stored.
    Applicability of ``document_type`` is enforced by the service via ``app.vehicle_rules``.
    """

    __tablename__ = "compliance_documents"
    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="compliance_documents_period_order"),
        CheckConstraint("amount IS NULL OR amount >= 0", name="compliance_documents_amount_nonnegative"),
        Index("ix_compliance_documents_current", "vehicle_id", "document_type", "end_date"),
    )

    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False
    )
    document_type: Mapped[str] = mapped_column(String(40), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(300), nullable=True)
    policy_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    renewed_from_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("compliance_documents.id", ondelete="SET NULL"), nullable=True
    )
    # The expense that records this document's cost. Deleting the expense only unlinks it.
    expense_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("expenses.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    vehicle = relationship("Vehicle", foreign_keys=[vehicle_id], lazy="raise")
    creator = relationship("User", foreign_keys=[created_by], lazy="raise")
    expense = relationship("Expense", back_populates="compliance_document", lazy="raise")
