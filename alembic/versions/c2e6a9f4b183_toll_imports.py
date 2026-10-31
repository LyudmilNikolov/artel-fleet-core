"""Toll imports: upload batches and the card transactions each toll expense was built from.

Revision ID: c2e6a9f4b183
Revises: a4d8e1b7c926

Only structure is created; existing expenses are not touched.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "c2e6a9f4b183"
down_revision = "a4d8e1b7c926"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "toll_import_batches",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("filename", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("uploaded_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rows", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("problems", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "toll_transactions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("external_id", sa.String(length=100), nullable=False),
        sa.Column("expense_id", sa.UUID(), nullable=False),
        sa.Column("import_batch_id", sa.UUID(), nullable=True),
        sa.Column("plate", sa.String(length=20), nullable=False),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("transaction_time", sa.Time(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("product", sa.String(length=100), nullable=True),
        sa.Column("network", sa.String(length=200), nullable=True),
        sa.Column("invoice_number", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["expense_id"], ["expenses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["import_batch_id"], ["toll_import_batches.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "external_id", name="uq_toll_transaction_source_external_id"),
    )
    op.create_index("ix_toll_transactions_expense_id", "toll_transactions", ["expense_id"])


def downgrade():
    op.drop_index("ix_toll_transactions_expense_id", table_name="toll_transactions")
    op.drop_table("toll_transactions")
    op.drop_table("toll_import_batches")
