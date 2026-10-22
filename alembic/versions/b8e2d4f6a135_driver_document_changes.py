"""Pending driver document changes (driver proposes, manager approves).

Revision ID: b8e2d4f6a135
Revises: a1d7e5b3c902

Adds ``driver_document_changes``. Existing data is untouched. Downgrade drops the table and so loses any
pending or decided change requests.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b8e2d4f6a135"
down_revision = "a1d7e5b3c902"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "driver_document_changes",
        sa.Column("driver_id", sa.UUID(), nullable=False),
        sa.Column("entry_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.String(length=10), nullable=False),
        sa.Column("document_type", sa.String(length=40), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("requested_by", sa.UUID(), nullable=True),
        sa.Column("decided_by", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("result_entry_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["driver_id"], ["drivers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entry_id"], ["driver_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["result_entry_id"], ["driver_documents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_driver_document_changes_status", "driver_document_changes", ["status", "created_at"])
    op.create_index(
        "ix_driver_document_changes_driver", "driver_document_changes", ["driver_id", "document_type", "status"]
    )


def downgrade():
    op.drop_index("ix_driver_document_changes_driver", table_name="driver_document_changes")
    op.drop_index("ix_driver_document_changes_status", table_name="driver_document_changes")
    op.drop_table("driver_document_changes")
