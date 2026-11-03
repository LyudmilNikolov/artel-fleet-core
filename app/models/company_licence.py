"""The company's international road haulage licence (the original; each tractor unit carries a certified copy)."""

import uuid
from datetime import date

from sqlalchemy import CheckConstraint, Date, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, TimestampMixin, UUIDPrimaryKey


class CompanyLicence(Base, UUIDPrimaryKey, TimestampMixin):
    """One validity period of the company licence. The entry with the latest ``end_date`` is the current one.

    Status (valid / expiring soon / expired) is computed from ``end_date`` and never stored. The scan of the
    original is stored through the documents feature (``entity_type = company_licence``).
    """

    __tablename__ = "company_licences"
    __table_args__ = (CheckConstraint("end_date >= start_date", name="company_licences_period_order"),)

    licence_number: Mapped[str] = mapped_column(String(60), nullable=False)
    holder: Mapped[str] = mapped_column(String(300), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    issued_by: Mapped[str | None] = mapped_column(String(300), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    renewed_from_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("company_licences.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
