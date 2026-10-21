"""Migration integration tests run only on an explicitly supplied disposable PostgreSQL URL.

Every test creates/drops its own database. Never points Alembic at the source database.
"""

import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).parents[2]


@pytest.fixture
def migration_db():
    configured = os.environ.get("TEST_DATABASE_URL_SYNC")
    if not configured:
        pytest.skip("TEST_DATABASE_URL_SYNC must explicitly identify a disposable PostgreSQL server")
    source = make_url(configured)
    if source.get_backend_name() != "postgresql":
        pytest.fail("Migration tests require PostgreSQL")
    admin = create_engine(source.set(database="postgres"), isolation_level="AUTOCOMMIT")
    name = "fleet_eur_test_" + uuid4().hex
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    sync_url = source.set(database=name)
    async_url = sync_url.set(drivername="postgresql+asyncpg")
    env = dict(
        os.environ,
        DATABASE_URL=async_url.render_as_string(hide_password=False),
        DATABASE_URL_SYNC=sync_url.render_as_string(hide_password=False),
        VAULT_ADDR="",
        VAULT_TOKEN="",
    )
    engine = create_engine(sync_url)
    try:
        yield engine, env
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


def alembic(env, *args):
    import sys

    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env, capture_output=True, text=True)


def test_fresh_chain_and_language_normalization(migration_db):
    engine, env = migration_db
    legacy = alembic(env, "upgrade", "35bbf56e6ff8")
    assert legacy.returncode == 0, legacy.stderr
    with engine.begin() as connection:
        for language in ["en", "ru", "kz", "tr", "xx", "bg"]:
            connection.execute(
                text(
                    "INSERT INTO users(id,email,username,full_name,hashed_password,role,is_active,language) VALUES(:id,:email,:username,'Name','hash','VIEWER',true,:language)"
                ),
                dict(id=uuid4(), email=language + "@test.com", username=language, language=language),
            )
    result = alembic(env, "upgrade", "head")
    assert result.returncode == 0, result.stderr
    with engine.connect() as connection:
        labels = (
            connection.execute(
                text("SELECT enumlabel FROM pg_enum JOIN pg_type ON pg_type.oid=enumtypid WHERE typname='currency'")
            )
            .scalars()
            .all()
        )
        assert labels == ["EUR"]
        currency_column = connection.execute(
            text(
                "SELECT column_default,is_nullable FROM information_schema.columns WHERE table_name='expenses' AND column_name='currency'"
            )
        ).one()
        assert "EUR" in currency_column.column_default
        assert currency_column.is_nullable == "NO"
        language_default = connection.execute(
            text(
                "SELECT column_default FROM information_schema.columns WHERE table_name='users' AND column_name='language'"
            )
        ).scalar_one()
        assert "bg" in language_default
        monetary_columns = connection.execute(
            text(
                "SELECT table_name,column_name,numeric_precision,numeric_scale FROM information_schema.columns WHERE (table_name,column_name) IN (('expenses','amount'),('expenses','fuel_price_per_liter'),('vehicles','purchase_price'),('maintenance_records','cost'),('contracts','amount'))"
            )
        ).all()
        assert len(monetary_columns) == 5
        for column in monetary_columns:
            assert column.numeric_scale == 2
            assert column.numeric_precision == (8 if column.column_name == "fuel_price_per_liter" else 12)
        languages = dict(connection.execute(text("SELECT username,language FROM users")).all())
        assert languages == dict(en="en", ru="bg", kz="bg", tr="bg", xx="bg", bg="bg")
    result = alembic(env, "check")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("field", ["expense", "purchase_price", "cost", "amount", "fuel_price_per_liter"])
def test_financial_fields_block_without_mutation(migration_db, field):
    engine, env = migration_db
    result = alembic(env, "upgrade", "35bbf56e6ff8")
    assert result.returncode == 0, result.stderr
    with engine.begin() as connection:
        # Minimal rows are copied from database metadata to avoid altering business validation.
        from app.models.vehicle import BodyType, FuelType, TransmissionType, VehicleStatus

        vehicle = dict(
            id=uuid4(),
            license_plate="A123",
            vin="12345678901234567",
            brand="B",
            model="M",
            year=2026,
            body_type=next(iter(BodyType)).name,
            fuel_type=next(iter(FuelType)).name,
            transmission=next(iter(TransmissionType)).name,
            status=VehicleStatus.ACTIVE.name,
        )
        connection.execute(
            text(
                "INSERT INTO vehicles(id,license_plate,vin,brand,model,year,body_type,fuel_type,transmission,status,current_mileage) VALUES(:id,:license_plate,:vin,:brand,:model,:year,:body_type,:fuel_type,:transmission,:status,0)"
            ),
            vehicle,
        )
        if field == "purchase_price":
            connection.execute(text("UPDATE vehicles SET purchase_price=0"))
        elif field in ["expense", "fuel_price_per_liter"]:
            connection.execute(
                text(
                    "INSERT INTO expenses(id,vehicle_id,category,amount,currency,date,fuel_price_per_liter) VALUES(:id,:vehicle_id,'FUEL',0,'KZT','2026-01-01',:fuel)"
                ),
                dict(id=uuid4(), vehicle_id=vehicle["id"], fuel=0 if field == "fuel_price_per_liter" else None),
            )
        elif field == "cost":
            connection.execute(
                text(
                    "INSERT INTO maintenance_records(id,vehicle_id,type,title,status,cost) VALUES(:id,:vehicle_id,'REPAIR','Title','SCHEDULED',0)"
                ),
                dict(id=uuid4(), vehicle_id=vehicle["id"]),
            )
        else:
            connection.execute(
                text(
                    "INSERT INTO contracts(id,vehicle_id,type,contractor,start_date,end_date,amount,payment_frequency,status,auto_renew) VALUES(:id,:vehicle_id,'INSURANCE_CASCO','Name','2026-01-01','2026-12-31',0,'ONE_TIME','ACTIVE',false)"
                ),
                dict(id=uuid4(), vehicle_id=vehicle["id"]),
            )
    result = alembic(env, "upgrade", "head")
    assert result.returncode != 0
    assert "Financial review required" in result.stderr
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "35bbf56e6ff8"
        assert connection.execute(
            text(
                "SELECT enumlabel FROM pg_enum JOIN pg_type ON pg_type.oid=enumtypid WHERE typname='currency' ORDER BY enumsortorder"
            )
        ).scalars().all() == ["KZT", "RUB", "USD", "TRY"]


def test_eur_financial_data_refuses_downgrade(migration_db):
    engine, env = migration_db
    result = alembic(env, "upgrade", "head")
    assert result.returncode == 0, result.stderr
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO vehicles(id,license_plate,vin,brand,model,year,body_type,fuel_type,transmission,status,current_mileage,purchase_price) VALUES(:id,'A123','12345678901234567','B','M',2026,'SEDAN','GASOLINE','MANUAL','ACTIVE',0,0)"
            ),
            dict(id=uuid4()),
        )
    result = alembic(env, "downgrade", "35bbf56e6ff8")
    assert result.returncode != 0
    assert "Financial review required" in result.stderr
    with engine.connect() as connection:
        assert connection.execute(text("SELECT purchase_price FROM vehicles")).scalar_one() == 0
        assert connection.execute(
            text("SELECT enumlabel FROM pg_enum JOIN pg_type ON pg_type.oid=enumtypid WHERE typname='currency'")
        ).scalars().all() == ["EUR"]
