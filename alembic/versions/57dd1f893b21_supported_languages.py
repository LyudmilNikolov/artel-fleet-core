"""Normalize language preferences to Bulgarian and English.

Revision ID: 57dd1f893b21
Revises: 46cc0e782a10
"""

import sqlalchemy as sa

from alembic import op

revision = "57dd1f893b21"
down_revision = "46cc0e782a10"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE users SET language = 'bg' WHERE language NOT IN ('bg', 'en')")
    op.alter_column("users", "language", server_default="bg", existing_type=sa.String(5))
    op.create_check_constraint("users_language_supported", "users", "language IN ('bg', 'en')")


def downgrade():
    op.drop_constraint("users_language_supported", "users", type_="check")
    op.alter_column("users", "language", server_default=None, existing_type=sa.String(5))
