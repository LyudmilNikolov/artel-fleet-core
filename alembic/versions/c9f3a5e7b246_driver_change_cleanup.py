"""Rejected and cancelled driver document changes are deleted, not kept.

Revision ID: c9f3a5e7b246
Revises: b8e2d4f6a135

A rejection now deletes the request and a manager can send it back for changes (status ``revision``).
Upgrade removes requests that were stored as ``rejected`` or ``cancelled`` before this change. Downgrade
cannot bring them back and does nothing.
"""

from alembic import op

revision = "c9f3a5e7b246"
down_revision = "b8e2d4f6a135"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("DELETE FROM driver_document_changes WHERE status IN ('rejected', 'cancelled')")


def downgrade():
    pass
