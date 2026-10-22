"""Reminder configuration: global settings, one rule per source, rule recipients.

Revision ID: f3c7a9d2b815
Revises: e8b2f4a6c1d3

Only structure is created; no rows. A source without a rule row uses the code defaults
(``app/notification_rules.py``), so new document types need no migration. Nothing is sent yet.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "f3c7a9d2b815"
down_revision = "e8b2f4a6c1d3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "notification_settings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("singleton", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("send_time", sa.Time(), server_default=sa.text("'08:00'"), nullable=False),
        sa.Column("timezone", sa.String(length=64), server_default=sa.text("'Europe/Sofia'"), nullable=False),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("singleton", name="notification_settings_single_row"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("singleton"),
    )
    op.create_table(
        "notification_rules",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("email_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("telegram_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("milestone_days", postgresql.ARRAY(sa.Integer()), nullable=False),
        sa.Column("repeat_every_days", sa.Integer(), nullable=True),
        sa.Column("stop_after_overdue_days", sa.Integer(), nullable=True),
        sa.Column("send_time", sa.Time(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "repeat_every_days IS NULL OR repeat_every_days >= 1", name="notification_rules_repeat_positive"
        ),
        sa.CheckConstraint(
            "stop_after_overdue_days IS NULL OR stop_after_overdue_days >= 1", name="notification_rules_stop_positive"
        ),
        sa.CheckConstraint("cardinality(milestone_days) BETWEEN 1 AND 10", name="notification_rules_milestone_count"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source"),
    )
    op.create_table(
        "notification_rule_recipients",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("rule_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("role", sa.String(length=30), nullable=True),
        sa.Column("address", sa.String(length=255), nullable=True),
        sa.CheckConstraint(
            "(kind <> 'user' OR user_id IS NOT NULL) AND (kind <> 'role' OR role IS NOT NULL)"
            " AND (kind NOT IN ('external_email', 'external_telegram') OR address IS NOT NULL)",
            name="notification_rule_recipients_target",
        ),
        sa.ForeignKeyConstraint(["rule_id"], ["notification_rules.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_notification_recipient_user",
        "notification_rule_recipients",
        ["rule_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'user'"),
    )
    op.create_index(
        "uq_notification_recipient_role",
        "notification_rule_recipients",
        ["rule_id", "role"],
        unique=True,
        postgresql_where=sa.text("kind = 'role'"),
    )
    op.create_index(
        "uq_notification_recipient_address",
        "notification_rule_recipients",
        ["rule_id", "kind", "address"],
        unique=True,
        postgresql_where=sa.text("address IS NOT NULL"),
    )


def downgrade():
    op.drop_table("notification_rule_recipients")
    op.drop_table("notification_rules")
    op.drop_table("notification_settings")
