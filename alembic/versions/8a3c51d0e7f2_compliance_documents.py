"""Replace contracts with per-vehicle compliance documents; requires an empty contracts table.

Revision ID: 8a3c51d0e7f2
Revises: 68ee2a794c32

No contract data is converted or relabelled. Upgrade refuses any remaining contract
rows or contract attachments; downgrade refuses any compliance documents.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "8a3c51d0e7f2"
down_revision = "68ee2a794c32"
branch_labels = None
depends_on = None


def _exists(sql: str) -> bool:
    return bool(op.get_bind().execute(sa.text(f"SELECT EXISTS ({sql})")).scalar())


def upgrade():
    op.execute("LOCK TABLE contracts, documents IN ACCESS EXCLUSIVE MODE")
    if _exists("SELECT 1 FROM contracts") or _exists("SELECT 1 FROM documents WHERE entity_type = 'CONTRACT'"):
        raise RuntimeError(
            "Compliance migration requires an empty contracts table and no contract attachments. "
            "Contract data is never converted automatically; review and remove it separately first."
        )
    op.drop_index("ix_contracts_vehicle_id", table_name="contracts")
    op.drop_index("ix_contracts_end_date", table_name="contracts")
    op.drop_table("contracts")
    for name in ("contract_type", "contract_status", "payment_frequency"):
        op.execute(f"DROP TYPE {name}")
    op.execute("ALTER TYPE entity_type RENAME VALUE 'CONTRACT' TO 'COMPLIANCE_DOCUMENT'")

    op.create_table(
        "compliance_documents",
        sa.Column("vehicle_id", sa.UUID(), nullable=False),
        sa.Column("document_type", sa.String(length=40), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("provider", sa.String(length=300), nullable=True),
        sa.Column("policy_number", sa.String(length=100), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("renewed_from_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("end_date >= start_date", name="compliance_documents_period_order"),
        sa.CheckConstraint("amount IS NULL OR amount >= 0", name="compliance_documents_amount_nonnegative"),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["renewed_from_id"], ["compliance_documents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_compliance_documents_current", "compliance_documents", ["vehicle_id", "document_type", "end_date"]
    )
    op.create_index("ix_compliance_documents_end_date", "compliance_documents", ["end_date"])


def downgrade():
    op.execute("LOCK TABLE compliance_documents, documents IN ACCESS EXCLUSIVE MODE")
    if _exists("SELECT 1 FROM compliance_documents") or _exists(
        "SELECT 1 FROM documents WHERE entity_type = 'COMPLIANCE_DOCUMENT'"
    ):
        raise RuntimeError(
            "Downgrade requires an empty compliance_documents table and no compliance attachments; "
            "compliance documents are never deleted or converted automatically."
        )
    op.drop_index("ix_compliance_documents_end_date", table_name="compliance_documents")
    op.drop_index("ix_compliance_documents_current", table_name="compliance_documents")
    op.drop_table("compliance_documents")
    op.execute("ALTER TYPE entity_type RENAME VALUE 'COMPLIANCE_DOCUMENT' TO 'CONTRACT'")

    contract_type = postgresql.ENUM(
        "LEASING", "RENTAL", "INSURANCE_CASCO", "INSURANCE_OSAGO", "WARRANTY", "SERVICE_CONTRACT", name="contract_type"
    )
    payment_frequency = postgresql.ENUM("ONE_TIME", "MONTHLY", "QUARTERLY", "ANNUAL", name="payment_frequency")
    contract_status = postgresql.ENUM("ACTIVE", "EXPIRED", "CANCELLED", "PENDING_RENEWAL", name="contract_status")
    for enum in (contract_type, payment_frequency, contract_status):
        enum.create(op.get_bind())
    op.create_table(
        "contracts",
        sa.Column("vehicle_id", sa.UUID(), nullable=False),
        sa.Column("type", postgresql.ENUM(name="contract_type", create_type=False), nullable=False),
        sa.Column("contractor", sa.String(length=300), nullable=False),
        sa.Column("contract_number", sa.String(length=100), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("payment_frequency", postgresql.ENUM(name="payment_frequency", create_type=False), nullable=False),
        sa.Column("status", postgresql.ENUM(name="contract_status", create_type=False), nullable=False),
        sa.Column("auto_renew", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_contracts_end_date", "contracts", ["end_date"], unique=False)
    op.create_index("ix_contracts_vehicle_id", "contracts", ["vehicle_id"], unique=False)
