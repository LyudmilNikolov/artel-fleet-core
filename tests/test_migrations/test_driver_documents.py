"""Driver documents migration: legacy data is copied, unmapped data is guarded, downgrade is guarded."""

from uuid import uuid4

from sqlalchemy import text

from tests.test_migrations.test_postgres_eur import alembic, migration_db  # noqa: F401

# Imported pytest fixture is intentionally injected by parameter name.
# ruff: noqa: F811

PREVIOUS = "d7a4f2c61b85"
HEAD = "e3b9c4a71f26"
LEGACY = ("employee_id", "license_number", "license_category", "license_expiry", "medical_expiry")


def insert_driver(c, name, **legacy):
    driver = uuid4()
    columns = {"id": driver, "full_name": name, **legacy}
    c.execute(
        text(
            f"INSERT INTO drivers({','.join(columns)},status) VALUES({','.join(':' + k for k in columns)},'ACTIVE')"
        ),
        columns,
    )
    return driver


def driver_columns(c):
    return set(
        c.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'drivers'")).scalars()
    )


def entity_labels(c):
    return (
        c.execute(
            text(
                "SELECT enumlabel FROM pg_enum JOIN pg_type ON pg_type.oid = enumtypid WHERE typname = 'entity_type' ORDER BY enumsortorder"
            )
        )
        .scalars()
        .all()
    )


def entries(c, driver):
    return c.execute(
        text(
            "SELECT document_type, document_number, categories, start_date, end_date FROM driver_documents WHERE driver_id = :d ORDER BY document_type"
        ),
        {"d": driver},
    ).all()


def test_upgrade_copies_licence_and_medical_data_and_drops_columns(migration_db):
    engine, env = migration_db
    assert alembic(env, "upgrade", PREVIOUS).returncode == 0
    with engine.begin() as c:
        full = insert_driver(
            c, "Full", license_number="L-1", license_category="B, C", license_expiry="2030-05-01", medical_expiry="2027-01-31"
        )
        medical_only = insert_driver(c, "Medical", medical_expiry="2026-12-01")
        empty = insert_driver(c, "Empty")
        category_only = insert_driver(c, "Category", license_category="D")
    result = alembic(env, "upgrade", HEAD)
    assert result.returncode == 0, result.stderr
    with engine.connect() as c:
        assert driver_columns(c).isdisjoint(LEGACY)
        full_rows = entries(c, full)
        assert [(r[0], r[1], r[2], str(r[4])) for r in full_rows] == [
            ("driving_licence", "L-1", "B, C", "2030-05-01"),
            ("medical_certificate", None, None, "2027-01-31"),
        ]
        assert [r[0] for r in entries(c, medical_only)] == ["medical_certificate"]
        assert entries(c, empty) == []
        assert [(r[0], r[1], r[2], r[4]) for r in entries(c, category_only)] == [("driving_licence", None, "D", None)]
        assert "DRIVER_DOCUMENT" in entity_labels(c)
        indexes = set(c.execute(text("SELECT indexname FROM pg_indexes WHERE tablename = 'driver_documents'")).scalars())
        assert {"ix_driver_documents_current", "ix_driver_documents_end_date"} <= indexes
        on_delete = c.execute(
            text(
                "SELECT confdeltype FROM pg_constraint WHERE conrelid = 'driver_documents'::regclass AND contype = 'f' "
                "AND confrelid = 'drivers'::regclass"
            )
        ).scalar()
        assert on_delete == "c"  # ON DELETE CASCADE


def test_cascade_removes_entries_with_the_driver(migration_db):
    engine, env = migration_db
    assert alembic(env, "upgrade", PREVIOUS).returncode == 0
    with engine.begin() as c:
        driver = insert_driver(c, "Cascade", license_number="L-9")
    assert alembic(env, "upgrade", HEAD).returncode == 0
    with engine.begin() as c:
        assert len(entries(c, driver)) == 1
        c.execute(text("DELETE FROM drivers WHERE id = :d"), {"d": driver})
        assert c.execute(text("SELECT count(*) FROM driver_documents")).scalar() == 0


def test_employee_id_values_block_upgrade_unless_confirmed(migration_db):
    engine, env = migration_db
    assert alembic(env, "upgrade", PREVIOUS).returncode == 0
    with engine.begin() as c:
        driver = insert_driver(c, "Has id", employee_id="EMP-1", license_number="L-1")
    refused = alembic(env, "upgrade", HEAD)
    assert refused.returncode != 0 and "employee_id" in refused.stderr
    with engine.connect() as c:  # nothing was changed
        assert "employee_id" in driver_columns(c)
        assert c.execute(text("SELECT to_regclass('driver_documents')")).scalar() is None
    confirmed = alembic(env, "-x", "drop_legacy_driver_fields=yes", "upgrade", HEAD)
    assert confirmed.returncode == 0, confirmed.stderr
    with engine.connect() as c:
        assert "employee_id" not in driver_columns(c)
        assert [r[0] for r in entries(c, driver)] == ["driving_licence"]


def test_downgrade_is_guarded_and_restores_the_schema(migration_db):
    engine, env = migration_db
    assert alembic(env, "upgrade", PREVIOUS).returncode == 0
    with engine.begin() as c:
        driver = insert_driver(c, "Keep", license_number="L-1")
    assert alembic(env, "upgrade", HEAD).returncode == 0

    refused = alembic(env, "downgrade", PREVIOUS)
    assert refused.returncode != 0 and "driver_documents" in refused.stderr
    with engine.begin() as c:
        assert len(entries(c, driver)) == 1  # data is never deleted implicitly
        c.execute(text("DELETE FROM driver_documents"))
        c.execute(
            text(
                "INSERT INTO documents(id,entity_type,entity_id,type,filename,s3_key,mime_type,size_bytes) "
                "VALUES(:id,'DRIVER_DOCUMENT',:e,'SCAN','a.pdf','k','application/pdf',1)"
            ),
            {"id": uuid4(), "e": uuid4()},
        )
    refused_files = alembic(env, "downgrade", PREVIOUS)
    assert refused_files.returncode != 0 and "attachments" in refused_files.stderr
    with engine.begin() as c:
        c.execute(text("DELETE FROM documents"))

    done = alembic(env, "downgrade", PREVIOUS)
    assert done.returncode == 0, done.stderr
    with engine.connect() as c:
        assert set(LEGACY) <= driver_columns(c)
        assert "DRIVER_DOCUMENT" not in entity_labels(c)
        assert c.execute(text("SELECT to_regclass('driver_documents')")).scalar() is None
        unique = c.execute(
            text("SELECT count(*) FROM pg_constraint WHERE conname = 'drivers_employee_id_key' AND contype = 'u'")
        ).scalar()
        assert unique == 1
    again = alembic(env, "upgrade", HEAD)  # the chain can be re-applied
    assert again.returncode == 0, again.stderr


def test_head_matches_models(migration_db):
    engine, env = migration_db
    assert alembic(env, "upgrade", "head").returncode == 0
    check = alembic(env, "check")
    assert check.returncode == 0, check.stdout + check.stderr
