import enum
import uuid
from decimal import Decimal

from sqlalchemy import Boolean, Date, Enum, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base, TimestampMixin, UUIDPrimaryKey


class ContractType(str, enum.Enum):
    LEASING = "leasing"
    RENTAL = "rental"
    INSURANCE_CASCO = "insurance_casco"
    INSURANCE_OSAGO = "insurance_osago"
    WARRANTY = "warranty"
    SERVICE_CONTRACT = "service_contract"


class ContractStatus(str, enum.Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    PENDING_RENEWAL = "pending_renewal"


class PaymentFrequency(str, enum.Enum):
    ONE_TIME = "one_time"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"


class Contract(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "contracts"

    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[ContractType] = mapped_column(Enum(ContractType, name="contract_type"), nullable=False)
    contractor: Mapped[str] = mapped_column(String(300), nullable=False)
    contract_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    start_date: Mapped[str] = mapped_column(Date, nullable=False)
    end_date: Mapped[str] = mapped_column(Date, nullable=False, index=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    payment_frequency: Mapped[PaymentFrequency] = mapped_column(
        Enum(PaymentFrequency, name="payment_frequency"), default=PaymentFrequency.ONE_TIME, nullable=False
    )
    status: Mapped[ContractStatus] = mapped_column(
        Enum(ContractStatus, name="contract_status"), default=ContractStatus.ACTIVE, nullable=False
    )
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    vehicle = relationship("Vehicle", foreign_keys=[vehicle_id])
    creator = relationship("User", foreign_keys=[created_by])
