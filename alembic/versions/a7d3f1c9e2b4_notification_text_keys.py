"""Translation keys for stored notification messages.

Revision ID: a7d3f1c9e2b4
Revises: c2e6a9f4b183

Adds three nullable columns so a message can be rendered in the language the reader uses now:
``title_key`` / ``message_key`` (translation keys) and ``params`` (their values). Existing rows keep ``NULL``
and are shown with the text they were stored with. Downgrade drops the columns; the stored text stays.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "a7d3f1c9e2b4"
down_revision = "c2e6a9f4b183"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("notifications", sa.Column("title_key", sa.String(length=100), nullable=True))
    op.add_column("notifications", sa.Column("message_key", sa.String(length=100), nullable=True))
    op.add_column("notifications", sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade():
    op.drop_column("notifications", "params")
    op.drop_column("notifications", "message_key")
    op.drop_column("notifications", "title_key")
