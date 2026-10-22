"""Per-driver tracked documents; licence and medical data move out of ``drivers``.

Revision ID: e3b9c4a71f26
Revises: d7a4f2c61b85

Upgrade copies ``license_number/license_category/license_expiry`` into one ``driving_licence``
entry and ``medical_expiry`` into one ``medical_certificate`` entry per driver, then drops the
five legacy columns. ``employee_id`` has no replacement: upgrade refuses while any value exists
unless run as ``alembic -x drop_legacy_driver_fields=yes upgrade head``.

Downgrade refuses while any driver document or driver-document attachment exists.
"""

import sqlalchemy as sa

from alembic import context, op

revision = "e3b9c4a71f26"
down_revision = "d7a4f2c61b85"
branch_labels = None
depends_on = None


def _exists(sql: str) -> bool:
    return bool(op.get_bind().execute(sa.text(f"SELECT EXISTS ({sql})")).scalar())


def upgrade():
    op.execute("LOCK TABLE drivers, documents IN ACCESS EXCLUSIVE MODE")
    allow_drop = context.get_x_argument(as_dictionary=True).get("drop_legacy_driver_fields") == "yes"
    if not allow_drop and _exists("SELECT 1 FROM drivers WHERE employee_id IS NOT NULL"):
        raise RuntimeError(
            "drivers.employee_id has values and has no replacement. Review them, then run "
            "`alembic -x drop_legacy_driver_fields=yes upgrade head` to drop the column."
        )

    op.execute("ALTER TYPE entity_type ADD VALUE IF NOT EXISTS 'DRIVER_DOCUMENT'")

    op.create_table(
        "driver_documents",
        sa.Column("driver_id", sa.UUID(), nullable=False),
        sa.Column("document_type", sa.String(length=40), nullable=False),
        sa.Column("document_number", sa.String(length=100), nullable=True),
        sa.Column("categories", sa.String(length=50), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("renewed_from_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR end_date >= start_date",
            name="driver_documents_period_order",
        ),
        sa.ForeignKeyConstraint(["driver_id"], ["drivers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["renewed_from_id"], ["driver_documents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_driver_documents_current", "driver_documents", ["driver_id", "document_type", "end_date"])
    op.create_index("ix_driver_documents_end_date", "driver_documents", ["end_date"])

    op.execute(
        """
        INSERT INTO driver_documents (id, driver_id, document_type, document_number, categories, end_date)
        SELECT gen_random_uuid(), id, 'driving_licence', license_number, license_category, license_expiry
        FROM drivers
        WHERE license_number IS NOT NULL OR license_category IS NOT NULL OR license_expiry IS NOT NULL
        """
    )
    op.execute(
        """
        INSERT INTO driver_documents (id, driver_id, document_type, end_date)
        SELECT gen_random_uuid(), id, 'medical_certificate', medical_expiry
        FROM drivers
        WHERE medical_expiry IS NOT NULL
        """
    )

    op.drop_constraint("drivers_employee_id_key", "drivers", type_="unique")
    for column in ("employee_id", "license_number", "license_category", "license_expiry", "medical_expiry"):
        op.drop_column("drivers", column)


def downgrade():
    op.execute("LOCK TABLE driver_documents, documents IN ACCESS EXCLUSIVE MODE")
    if _exists("SELECT 1 FROM driver_documents") or _exists(
        "SELECT 1 FROM documents WHERE entity_type = 'DRIVER_DOCUMENT'"
    ):
        raise RuntimeError(
            "Downgrade requires an empty driver_documents table and no driver document attachments; "
            "driver documents are never deleted or converted automatically."
        )
    op.add_column("drivers", sa.Column("employee_id", sa.String(length=50), nullable=True))
    op.add_column("drivers", sa.Column("license_number", sa.String(length=50), nullable=True))
    op.add_column("drivers", sa.Column("license_category", sa.String(length=20), nullable=True))
    op.add_column("drivers", sa.Column("license_expiry", sa.Date(), nullable=True))
    op.add_column("drivers", sa.Column("medical_expiry", sa.Date(), nullable=True))
    op.create_unique_constraint("drivers_employee_id_key", "drivers", ["employee_id"])

    op.drop_index("ix_driver_documents_end_date", table_name="driver_documents")
    op.drop_index("ix_driver_documents_current", table_name="driver_documents")
    op.drop_table("driver_documents")

    # PostgreSQL cannot drop an enum label: rebuild the type without DRIVER_DOCUMENT.
    op.execute("ALTER TYPE entity_type RENAME TO entity_type_old")
    op.execute(
        "CREATE TYPE entity_type AS ENUM ('VEHICLE', 'DRIVER', 'MAINTENANCE', 'COMPLIANCE_DOCUMENT', 'EXPENSE')"
    )
    op.execute(
        "ALTER TABLE documents ALTER COLUMN entity_type TYPE entity_type USING entity_type::text::entity_type"
    )
    op.execute("DROP TYPE entity_type_old")
