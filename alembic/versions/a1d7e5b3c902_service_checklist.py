"""Service checklist on maintenance records; filters stop being service items.

Revision ID: a1d7e5b3c902
Revises: f4c8d2e91a57

Adds ``maintenance_records.service_checklist`` (JSONB list). Records whose service item was
``oil_filter``, ``air_filter`` or ``fuel_filter`` become an ``engine_oil`` service with that filter ticked
in the checklist. Downgrade refuses while any checklist exists unless run as
``alembic -x drop_service_checklist=yes downgrade -1``; it then drops the column (filters ticked
on engine oil services are lost, they are not turned back into items).
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op

revision = "a1d7e5b3c902"
down_revision = "f4c8d2e91a57"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("maintenance_records", sa.Column("service_checklist", postgresql.JSONB(), nullable=True))
    op.execute(
        """
        UPDATE maintenance_records
        SET service_checklist = to_jsonb(ARRAY[service_item]), service_item = 'engine_oil'
        WHERE service_item IN ('oil_filter', 'air_filter', 'fuel_filter')
        """
    )


def downgrade():
    in_use = op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM maintenance_records WHERE service_checklist IS NOT NULL)")).scalar()
    if in_use and context.get_x_argument(as_dictionary=True).get("drop_service_checklist") != "yes":
        raise RuntimeError(
            "Service checklists exist and have no place in the previous schema. Review them, then run "
            "`alembic -x drop_service_checklist=yes downgrade -1` to drop the column."
        )
    op.drop_column("maintenance_records", "service_checklist")
