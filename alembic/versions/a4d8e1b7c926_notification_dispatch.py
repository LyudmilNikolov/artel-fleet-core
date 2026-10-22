"""Reminder dispatch state: stops, delivery log, daily runs.

Revision ID: a4d8e1b7c926
Revises: f3c7a9d2b815

Only structure is created; no rows.
"""

import sqlalchemy as sa

from alembic import op

revision = "a4d8e1b7c926"
down_revision = "f3c7a9d2b815"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "notification_stops",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("stopped_by", sa.UUID(), nullable=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("resumed_by", sa.UUID(), nullable=True),
        sa.Column("resumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["stopped_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resumed_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_notification_stops_active",
        "notification_stops",
        ["entity_type", "entity_id", "end_date"],
        unique=True,
        postgresql_where=sa.text("resumed_at IS NULL"),
    )
    op.create_index("ix_notification_stops_entity", "notification_stops", ["entity_type", "entity_id"])

    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("recipient", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("point", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error", sa.String(length=100), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'sent', 'failed')", name="notification_deliveries_status"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "entity_type",
            "entity_id",
            "end_date",
            "channel",
            "recipient",
            "point",
            name="uq_notification_deliveries_point",
        ),
    )
    op.create_index("ix_notification_deliveries_entity", "notification_deliveries", ["entity_type", "entity_id"])

    op.create_table(
        "notification_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("planned", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("sent", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("failed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_date", "source", name="uq_notification_runs_day_source"),
    )


def downgrade():
    op.drop_table("notification_runs")
    op.drop_table("notification_deliveries")
    op.drop_table("notification_stops")
