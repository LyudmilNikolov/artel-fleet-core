"""Guarded contracts -> compliance documents migration on disposable PostgreSQL databases."""

from uuid import uuid4

from sqlalchemy import text

from scripts.check_eur_readiness import FINANCIAL_COUNTS_SQL, optional_amount_counts
from scripts.vehicle_demo_cleanup import snapshot
from tests.test_migrations.test_postgres_eur import alembic, migration_db  # noqa: F401

# Imported pytest fixture is intentionally injected by parameter name.
# ruff: noqa: F811

PREVIOUS = "68ee2a794c32"
HEAD = "8a3c51d0e7f2"


def insert_vehicle(c):
    vehicle = uuid4()
    c.execute(
        text(
            "INSERT INTO vehicles(id,vehicle_type,license_plate,brand,model,department,status) VALUES(:id,'tractor_unit','CA1234AB','B','M','Dept','ACTIVE')"
        ),
        {"id": vehicle},
    )
    return vehicle


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


def test_upgrade_refuses_contract_data(migration_db):
    engine, env = migration_db
    assert alembic(env, "upgrade", PREVIOUS).returncode == 0
    with engine.begin() as c:
        vehicle = insert_vehicle(c)
        contract = uuid4()
        c.execute(
            text(
                "INSERT INTO contracts(id,vehicle_id,type,contractor,start_date,end_date,amount,payment_frequency,status,auto_renew) VALUES(:id,:vehicle,'INSURANCE_CASCO','Name','2026-01-01','2026-12-31',10,'ONE_TIME','ACTIVE',false)"
            ),
            {"id": contract, "vehicle": vehicle},
        )
        assert optional_amount_counts(c) == {"contract_amounts": 1}
    result = alembic(env, "upgrade", "head")
    assert result.returncode != 0 and "empty contracts table" in result.stderr
    with engine.begin() as c:
        assert c.execute(text("SELECT count(*) FROM contracts")).scalar() == 1
        assert snapshot(c)["entities"]["contract"] == [str(contract)]
        c.execute(text("DELETE FROM contracts"))
        c.execute(
            text(
                "INSERT INTO documents(id,entity_type,entity_id,type,filename,s3_key,mime_type,size_bytes) VALUES(:id,'CONTRACT',:entity,'CONTRACT','f','k','application/pdf',1)"
            ),
            {"id": uuid4(), "entity": contract},
        )
    result = alembic(env, "upgrade", "head")
    assert result.returncode != 0 and "contract attachments" in result.stderr
    with engine.begin() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == PREVIOUS
        c.execute(text("DELETE FROM documents"))
    result = alembic(env, "upgrade", "head")
    assert result.returncode == 0, result.stderr
    with engine.begin() as c:
        assert "COMPLIANCE_DOCUMENT" in entity_labels(c) and "CONTRACT" not in entity_labels(c)
        assert c.execute(text("SELECT to_regclass('contracts')")).scalar() is None
        assert c.execute(text("SELECT count(*) FROM vehicles")).scalar() == 1
        dict(c.execute(text(FINANCIAL_COUNTS_SQL)).mappings().one())
        assert optional_amount_counts(c) == {"compliance_amounts": 0}
        assert "compliance_document" in snapshot(c)["entities"]
    assert alembic(env, "check").returncode == 0


def test_downgrade_refuses_compliance_data(migration_db):
    engine, env = migration_db
    assert alembic(env, "upgrade", "head").returncode == 0
    with engine.begin() as c:
        vehicle = insert_vehicle(c)
        c.execute(
            text(
                "INSERT INTO compliance_documents(id,vehicle_id,document_type,start_date,end_date) VALUES(:id,:vehicle,'tachograph','2026-01-01','2027-12-31')"
            ),
            {"id": uuid4(), "vehicle": vehicle},
        )
        failed = False
        try:
            with c.begin_nested():
                c.execute(
                    text(
                        "INSERT INTO compliance_documents(id,vehicle_id,document_type,start_date,end_date) VALUES(:id,:vehicle,'casco','2026-02-01','2026-01-01')"
                    ),
                    {"id": uuid4(), "vehicle": vehicle},
                )
        except Exception as exc:  # noqa: BLE001 - asserting the check constraint below
            failed = "compliance_documents_period_order" in str(exc)
        assert failed
    result = alembic(env, "downgrade", PREVIOUS)
    assert result.returncode != 0 and "empty compliance_documents table" in result.stderr
    with engine.begin() as c:
        assert c.execute(text("SELECT count(*) FROM compliance_documents")).scalar() == 1
        c.execute(text("DELETE FROM compliance_documents"))
    result = alembic(env, "downgrade", PREVIOUS)
    assert result.returncode == 0, result.stderr
    with engine.begin() as c:
        assert "CONTRACT" in entity_labels(c)
        assert c.execute(text("SELECT to_regclass('compliance_documents')")).scalar() is None
        assert c.execute(text("SELECT count(*) FROM contracts")).scalar() == 0
    assert alembic(env, "upgrade", "head").returncode == 0
