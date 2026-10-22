"""A driver is assigned to at most one vehicle.

Revision ID: d1a8c6f2b974
Revises: c9f3a5e7b246

Adds a partial unique index on ``vehicles.assigned_driver_id``. Upgrade refuses while a driver is assigned to
more than one vehicle: review those assignments (remove all but one), then run the upgrade again.
"""

import sqlalchemy as sa

from alembic import op

revision = "d1a8c6f2b974"
down_revision = "c9f3a5e7b246"
branch_labels = None
depends_on = None


def upgrade():
    rows = (
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT d.full_name, count(*) FROM vehicles v JOIN drivers d ON d.id = v.assigned_driver_id
                GROUP BY d.id, d.full_name HAVING count(*) > 1
                """
            )
        )
        .all()
    )
    if rows:
        listing = ", ".join(f"{name} ({count} vehicles)" for name, count in rows)
        raise RuntimeError(
            "Some drivers are assigned to more than one vehicle: "
            + listing
            + ". A driver can have only one vehicle. Remove the extra assignments, then upgrade again."
        )
    op.create_index(
        "uq_vehicles_assigned_driver",
        "vehicles",
        ["assigned_driver_id"],
        unique=True,
        postgresql_where=sa.text("assigned_driver_id IS NOT NULL"),
    )


def downgrade():
    op.drop_index("uq_vehicles_assigned_driver", table_name="vehicles")
