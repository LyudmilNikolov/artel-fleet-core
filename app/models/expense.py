import enum
import uuid
from decimal import Decimal

from sqlalchemy import Date, Enum, Float, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.models import Base, TimestampMixin, UUIDPrimaryKey


class ExpenseCategory(str, enum.Enum):
    FUEL = "fuel"
    PARTS = "parts"
    SERVICE = "service"
    INSURANCE = "insurance"
    TAX = "tax"
    FINE = "fine"
    PARKING = "parking"
    TOLL = "toll"
    WASHING = "washing"
    OTHER = "other"
    # Categories that mirror a Documents & Deadlines type (values equal ComplianceType values).
    CIVIL_LIABILITY = "civil_liability"
    CASCO = "casco"
    TECHNICAL_INSPECTION = "technical_inspection"
    TACHOGRAPH = "tachograph"


class Currency(str, enum.Enum):
    EUR = "EUR"


class Expense(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "expenses"

    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    driver_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id", ondelete="SET NULL"), nullable=True
    )
    category: Mapped[ExpenseCategory] = mapped_column(Enum(ExpenseCategory, name="expense_category"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[Currency] = mapped_column(
        Enum(Currency, name="currency"), default=Currency.EUR, server_default="EUR", nullable=False
    )
    date: Mapped[str] = mapped_column(Date, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    vendor: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # Fuel sub-fields
    fuel_liters: Mapped[float | None] = mapped_column(Float, nullable=True)
    fuel_price_per_liter: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    fuel_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    mileage_at_refuel: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    vehicle = relationship("Vehicle", foreign_keys=[vehicle_id], lazy="joined")
    driver = relationship("Driver", foreign_keys=[driver_id])
    creator = relationship("User", foreign_keys=[created_by])
    # The Documents & Deadlines entry this cost belongs to, if any (the document holds the link).
    compliance_document = relationship("ComplianceDocument", back_populates="expense", uselist=False, lazy="selectin")

    @property
    def compliance_document_id(self) -> uuid.UUID | None:
        return self.compliance_document.id if self.compliance_document else None

    @validates("currency")
    def validate_currency(self, key, value):
        return Currency(value)
