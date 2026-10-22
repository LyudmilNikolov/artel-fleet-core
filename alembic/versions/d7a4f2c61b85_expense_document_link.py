"""Document-backed expense categories and the expense link on compliance documents.

Revision ID: d7a4f2c61b85
Revises: c5e1a7b90d34

Adds four expense categories that mirror the Documents & Deadlines types and a unique, nullable
``compliance_documents.expense_id``. Existing rows are untouched. Downgrade refuses while any
expense uses a new category or any document is linked, so financial data is never relabelled.
"""

import sqlalchemy as sa

from alembic import op

revision = "d7a4f2c61b85"
down_revision = "c5e1a7b90d34"
branch_labels = None
depends_on = None

NEW = ("CIVIL_LIABILITY", "CASCO", "TECHNICAL_INSPECTION", "TACHOGRAPH")
OLD = ("FUEL", "PARTS", "SERVICE", "INSURANCE", "TAX", "FINE", "PARKING", "TOLL", "WASHING", "OTHER")


def upgrade():
    for label in NEW:
        op.execute(f"ALTER TYPE expense_category ADD VALUE IF NOT EXISTS '{label}'")
    op.add_column("compliance_documents", sa.Column("expense_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_compliance_documents_expense_id",
        "compliance_documents",
        "expenses",
        ["expense_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint("uq_compliance_documents_expense_id", "compliance_documents", ["expense_id"])


def downgrade():
    op.execute("LOCK TABLE expenses, compliance_documents IN ACCESS EXCLUSIVE MODE")
    bind = op.get_bind()
    labels = ", ".join(f"'{label}'" for label in NEW)
    if (
        bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM expenses WHERE category::text IN ({labels}))")).scalar()
        or bind.execute(
            sa.text("SELECT EXISTS (SELECT 1 FROM compliance_documents WHERE expense_id IS NOT NULL)")
        ).scalar()
    ):
        raise RuntimeError(
            "Downgrade refused: expenses use document categories or documents are linked to expenses. "
            "Financial records are never relabelled or unlinked automatically."
        )
    op.drop_constraint("uq_compliance_documents_expense_id", "compliance_documents", type_="unique")
    op.drop_constraint("fk_compliance_documents_expense_id", "compliance_documents", type_="foreignkey")
    op.drop_column("compliance_documents", "expense_id")
    old = ", ".join(f"'{label}'" for label in OLD)
    op.execute(f"CREATE TYPE expense_category_old AS ENUM ({old})")
    op.execute(
        "ALTER TABLE expenses ALTER COLUMN category TYPE expense_category_old USING category::text::expense_category_old"
    )
    op.execute("DROP TYPE expense_category")
    op.execute("ALTER TYPE expense_category_old RENAME TO expense_category")
