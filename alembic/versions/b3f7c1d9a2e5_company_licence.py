"""Company licence (international haulage licence of the company).

Revision ID: b3f7c1d9a2e5
Revises: a7d3f1c9e2b4

Adds the ``company_licences`` table and the ``COMPANY_LICENCE`` label of the ``entity_type`` enum (scans of the
licence use the documents feature). Purely additive: no existing row is touched. Downgrade refuses while a company
licence or one of its scans exists, because dropping them would lose user-entered data. A PostgreSQL enum label
cannot be removed, so downgrade leaves ``COMPANY_LICENCE`` in ``entity_type``; it is unused and harmless.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b3f7c1d9a2e5"
down_revision = "a7d3f1c9e2b4"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE entity_type ADD VALUE IF NOT EXISTS 'COMPANY_LICENCE'")
    op.create_table(
        "company_licences",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("licence_number", sa.String(length=60), nullable=False),
        sa.Column("holder", sa.String(length=300), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("issued_by", sa.String(length=300), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("renewed_from_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("end_date >= start_date", name="company_licences_period_order"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["renewed_from_id"], ["company_licences.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_company_licences_end_date"), "company_licences", ["end_date"], unique=False)


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM company_licences LIMIT 1")).first():
        raise RuntimeError("Refusing to drop company_licences: it holds data. Export or delete the rows first.")
    if bind.execute(sa.text("SELECT 1 FROM documents WHERE entity_type = 'COMPANY_LICENCE' LIMIT 1")).first():
        raise RuntimeError("Refusing to downgrade: scans of the company licence exist. Delete them first.")
    op.drop_index(op.f("ix_company_licences_end_date"), table_name="company_licences")
    op.drop_table("company_licences")
