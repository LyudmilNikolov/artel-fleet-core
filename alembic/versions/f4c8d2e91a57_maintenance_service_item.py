"""Service item on maintenance records (engine oil, gearbox oil, ...).

Revision ID: f4c8d2e91a57
Revises: e3b9c4a71f26

Adds a nullable string column; existing records keep ``NULL`` (no item). Downgrade drops the column
and with it the item of every record.
"""

import sqlalchemy as sa

from alembic import op

revision = "f4c8d2e91a57"
down_revision = "e3b9c4a71f26"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("maintenance_records", sa.Column("service_item", sa.String(length=40), nullable=True))


def downgrade():
    op.drop_column("maintenance_records", "service_item")
