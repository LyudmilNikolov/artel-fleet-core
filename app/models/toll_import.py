"""Toll imports from fuel-card exports (Shell Priced Transaction CSV).

``TollTransaction`` remembers each imported card transaction and the expense it went into, so uploading an
overlapping file again only adds what is new. Transactions of one vehicle and day share one toll expense.
``TollImportBatch`` is one upload: its preview, what was applied and the lines that could not be imported.
"""

import enum
import uuid
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Time, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base, UUIDPrimaryKey


class TollImportStatus(enum.StrEnum):
    PREVIEW = "preview"
    APPLIED = "applied"


class TollImportBatch(Base, UUIDPrimaryKey):
    __tablename__ = "toll_import_batches"

    source: Mapped[str] = mapped_column(String(30), nullable=False)
    filename: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=TollImportStatus.PREVIEW)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Parsed usable lines, kept only until the preview is applied.
    rows: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Lines that were not imported: [{line, transaction_id, plate, date, amount, reason, field}]
    problems: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    uploader = relationship("User", foreign_keys=[uploaded_by], lazy="joined")


class TollTransaction(Base, UUIDPrimaryKey):
    __tablename__ = "toll_transactions"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_toll_transaction_source_external_id"),)

    source: Mapped[str] = mapped_column(String(30), nullable=False)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    # Deleting the expense forgets its transactions, so a later upload adds them again.
    expense_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("expenses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    import_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("toll_import_batches.id", ondelete="SET NULL"), nullable=True
    )
    plate: Mapped[str] = mapped_column(String(20), nullable=False)
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    transaction_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    product: Mapped[str | None] = mapped_column(String(100), nullable=True)
    network: Mapped[str | None] = mapped_column(String(200), nullable=True)
    invoice_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    expense = relationship("Expense", back_populates="toll_transactions")
