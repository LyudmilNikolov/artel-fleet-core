"""A driver's proposed change to a tracked document, waiting for a manager's decision.

Drivers never write ``driver_documents`` directly: adding, editing or renewing an entry creates one of these
(status ``pending``). A manager approves it (the change is applied to the entry), sends it back for changes
(``revision``) or rejects it (the request is deleted). The proposed
values live in ``payload`` (full numbers, so this table is not part of the automatic audit; the service writes
masked audit entries instead).
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base, TimestampMixin, UUIDPrimaryKey


class ChangeAction(enum.StrEnum):
    CREATE = "create"  # a new document of a type
    EDIT = "edit"  # correct an existing entry
    RENEW = "renew"  # a new validity period; the old entry stays as history


class ChangeStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REVISION = "revision"  # sent back by the manager: the driver corrects and resubmits, or deletes it
    # Rejected and cancelled requests are deleted, not kept. These two values only exist for rows saved before that.
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class DriverDocumentChange(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "driver_document_changes"
    __table_args__ = (
        Index("ix_driver_document_changes_status", "status", "created_at"),
        Index("ix_driver_document_changes_driver", "driver_id", "document_type", "status"),
    )

    driver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id", ondelete="CASCADE"), nullable=False
    )
    # The entry that is edited or renewed; empty for a new document.
    entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("driver_documents.id", ondelete="CASCADE"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(10), nullable=False)
    document_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # document_number, categories, start_date, end_date, notes (dates as ISO strings)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default=ChangeStatus.PENDING.value)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The entry created or changed by an approval.
    result_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("driver_documents.id", ondelete="SET NULL"), nullable=True
    )

    driver = relationship("Driver", foreign_keys=[driver_id], lazy="raise")
    requester = relationship("User", foreign_keys=[requested_by], lazy="raise")
    decider = relationship("User", foreign_keys=[decided_by], lazy="raise")
