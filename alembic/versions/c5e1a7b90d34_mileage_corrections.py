"""Mileage correction readings and a timestamp-independent insertion order.

Revision ID: c5e1a7b90d34
Revises: 8a3c51d0e7f2

A correction is a deliberate lower reading that starts a new baseline; older readings are kept.
Downgrade refuses while any correction exists, because dropping the flag would silently turn
corrections into ordinary readings and invalidate the monotonic baseline.
"""

import sqlalchemy as sa

from alembic import op

revision = "c5e1a7b90d34"
down_revision = "8a3c51d0e7f2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("mileage_logs", sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False))
    op.add_column("mileage_logs", sa.Column("is_correction", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.create_index("ix_mileage_logs_vehicle_seq", "mileage_logs", ["vehicle_id", "seq"])


def downgrade():
    op.execute("LOCK TABLE mileage_logs IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM mileage_logs WHERE is_correction)")).scalar():
        raise RuntimeError(
            "Downgrade refused: correction mileage readings exist. They are never discarded or converted automatically."
        )
    op.drop_index("ix_mileage_logs_vehicle_seq", table_name="mileage_logs")
    op.drop_column("mileage_logs", "is_correction")
    op.drop_column("mileage_logs", "seq")
