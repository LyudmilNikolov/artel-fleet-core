"""Fuel liters become an exact decimal (NUMERIC(10,2)) instead of a float.

Revision ID: e8b2f4a6c1d3
Revises: d1a8c6f2b974

Upgrade refuses while a stored value has more than two decimals or does not fit, so no entry is silently
rounded: correct those expenses, then upgrade again. Downgrade converts back to a float.
"""

import sqlalchemy as sa

from alembic import op

revision = "e8b2f4a6c1d3"
down_revision = "d1a8c6f2b974"
branch_labels = None
depends_on = None


def upgrade():
    rows = (
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT id, fuel_liters FROM expenses
                WHERE fuel_liters IS NOT NULL
                  AND (fuel_liters::numeric <> round(fuel_liters::numeric, 2) OR abs(fuel_liters) >= 100000000)
                ORDER BY date
                LIMIT 10
                """
            )
        )
        .all()
    )
    if rows:
        listing = ", ".join(f"{expense_id} ({liters})" for expense_id, liters in rows)
        raise RuntimeError(
            "Some fuel expenses have liters with more than two decimals or too large to store exactly: "
            + listing
            + ". Correct them, then upgrade again."
        )
    op.alter_column(
        "expenses",
        "fuel_liters",
        existing_type=sa.Float(),
        type_=sa.Numeric(10, 2),
        existing_nullable=True,
        postgresql_using="round(fuel_liters::numeric, 2)",
    )


def downgrade():
    op.alter_column(
        "expenses",
        "fuel_liters",
        existing_type=sa.Numeric(10, 2),
        type_=sa.Float(),
        existing_nullable=True,
        postgresql_using="fuel_liters::double precision",
    )
