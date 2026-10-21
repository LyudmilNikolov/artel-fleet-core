"""EUR-only schema, guarded against financial reinterpretation.

Revision ID: 46cc0e782a10
Revises: 35bbf56e6ff8
"""

from sqlalchemy import text

from alembic import op

revision = "46cc0e782a10"
down_revision = "35bbf56e6ff8"
branch_labels = None
depends_on = None


def _guard():
    op.execute("LOCK TABLE expenses, vehicles, maintenance_records, contracts IN ACCESS EXCLUSIVE MODE")
    populated = (
        op.get_bind()
        .execute(
            text("""
        SELECT EXISTS(SELECT 1 FROM expenses)
        OR EXISTS(SELECT 1 FROM vehicles WHERE purchase_price IS NOT NULL)
        OR EXISTS(SELECT 1 FROM maintenance_records WHERE cost IS NOT NULL)
        OR EXISTS(SELECT 1 FROM contracts WHERE amount IS NOT NULL)
    """)
        )
        .scalar_one()
    )
    if populated:
        raise RuntimeError(
            "Financial review required: EUR transition/downgrade blocked by existing financial values (including zero). Preserve original denominations and provenance; use an approved audited remediation migration. No automatic conversion or relabeling is permitted."
        )


def _replace(labels, default):
    op.execute("ALTER TABLE expenses ALTER COLUMN currency DROP DEFAULT")
    op.execute(f"CREATE TYPE currency_transition AS ENUM ({labels})")
    op.execute(
        "ALTER TABLE expenses ALTER COLUMN currency TYPE currency_transition USING currency::text::currency_transition"
    )
    op.execute("DROP TYPE currency")
    op.execute("ALTER TYPE currency_transition RENAME TO currency")
    if default:
        op.execute(f"ALTER TABLE expenses ALTER COLUMN currency SET DEFAULT '{default}'::currency")


def upgrade():
    _guard()
    _replace("'EUR'", "EUR")


def downgrade():
    _guard()
    _replace("'KZT', 'RUB', 'USD', 'TRY'", None)
