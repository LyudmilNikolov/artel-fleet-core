"""Document expense categories and link column: upgrade keeps data; downgrade refuses while they are in use."""

from uuid import uuid4

from sqlalchemy import text

from tests.test_migrations.test_postgres_eur import alembic, migration_db  # noqa: F401

# Imported pytest fixture is intentionally injected by parameter name.
# ruff: noqa: F811

PREVIOUS = "c5e1a7b90d34"


def labels(c):
    return (
        c.execute(
            text(
                "SELECT enumlabel FROM pg_enum JOIN pg_type ON pg_type.oid = enumtypid WHERE typname = 'expense_category' ORDER BY enumsortorder"
            )
        )
        .scalars()
        .all()
    )


def test_upgrade_downgrade_guard(migration_db):
    engine, env = migration_db
    assert alembic(env, "upgrade", PREVIOUS).returncode == 0
    vehicle, old_expense, new_expense, document = uuid4(), uuid4(), uuid4(), uuid4()
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO vehicles(id,vehicle_type,license_plate,brand,model,department,status) VALUES(:id,'van','CA1234AB','B','M','Dept','ACTIVE')"
            ),
            {"id": vehicle},
        )
        c.execute(
            text(
                "INSERT INTO expenses(id,vehicle_id,category,amount,currency,date) VALUES(:id,:vehicle,'INSURANCE',12.50,'EUR','2026-01-01')"
            ),
            {"id": old_expense, "vehicle": vehicle},
        )
        assert len(labels(c)) == 10
    result = alembic(env, "upgrade", "head")
    assert result.returncode == 0, result.stderr
    with engine.begin() as c:
        assert labels(c)[-4:] == ["CIVIL_LIABILITY", "CASCO", "TECHNICAL_INSPECTION", "TACHOGRAPH"]
        assert (
            str(c.execute(text("SELECT amount FROM expenses WHERE id = :id"), {"id": old_expense}).scalar()) == "12.50"
        )
        c.execute(
            text(
                "INSERT INTO expenses(id,vehicle_id,category,amount,currency,date) VALUES(:id,:vehicle,'CASCO',99,'EUR','2026-01-02')"
            ),
            {"id": new_expense, "vehicle": vehicle},
        )
        c.execute(
            text(
                "INSERT INTO compliance_documents(id,vehicle_id,document_type,start_date,end_date,amount,expense_id) VALUES(:id,:vehicle,'casco','2026-01-01','2026-12-31',99,:expense)"
            ),
            {"id": document, "vehicle": vehicle, "expense": new_expense},
        )
    assert alembic(env, "check").returncode == 0
    with engine.begin() as c:
        # One document per expense.
        try:
            with c.begin_nested():
                c.execute(
                    text(
                        "INSERT INTO compliance_documents(id,vehicle_id,document_type,start_date,end_date,expense_id) VALUES(:id,:vehicle,'casco','2027-01-01','2027-12-31',:expense)"
                    ),
                    {"id": uuid4(), "vehicle": vehicle, "expense": new_expense},
                )
            duplicate_accepted = True
        except Exception:  # noqa: BLE001 - asserting the unique constraint below
            duplicate_accepted = False
        assert not duplicate_accepted
    result = alembic(env, "downgrade", PREVIOUS)
    assert result.returncode != 0 and "Downgrade refused" in result.stderr
    with engine.begin() as c:
        c.execute(text("DELETE FROM compliance_documents"))
        c.execute(text("UPDATE expenses SET category = 'OTHER' WHERE id = :id"), {"id": new_expense})
    result = alembic(env, "downgrade", PREVIOUS)
    assert result.returncode == 0, result.stderr
    with engine.begin() as c:
        assert len(labels(c)) == 10
        assert c.execute(text("SELECT count(*) FROM expenses")).scalar() == 2
        assert (
            c.execute(text("SELECT category::text FROM expenses WHERE id = :id"), {"id": old_expense}).scalar()
            == "INSURANCE"
        )
        columns = (
            c.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name='compliance_documents'")
            )
            .scalars()
            .all()
        )
        assert "expense_id" not in columns
    assert alembic(env, "upgrade", "head").returncode == 0
