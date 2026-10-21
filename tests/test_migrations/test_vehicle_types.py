from uuid import uuid4

import pytest
from sqlalchemy import text

from scripts.vehicle_demo_cleanup import cleanup, snapshot
from tests.test_migrations.test_postgres_eur import alembic, migration_db  # noqa: F401

# Imported pytest fixture is intentionally injected by parameter name.
# ruff: noqa: F811


def test_vehicle_migration_guard_and_downgrade(migration_db):
    engine, env = migration_db
    assert alembic(env, "upgrade", "57dd1f893b21").returncode == 0
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO vehicles(id,license_plate,vin,brand,model,year,body_type,fuel_type,transmission,status,current_mileage) VALUES(:id,'OLD','12345678901234567','B','M',2026,'SEDAN','DIESEL','MANUAL','ACTIVE',0)"
            ),
            {"id": uuid4()},
        )
    result = alembic(env, "upgrade", "head")
    assert result.returncode != 0 and "empty vehicles table" in result.stderr
    with engine.begin() as c:
        assert c.execute(text("SELECT body_type FROM vehicles")).scalar() == "SEDAN"
        captured = snapshot(c)
        assert not captured["ready"]
        cleanup(c, captured)
        assert snapshot(c)["ready"]
        cleanup(c, snapshot(c))
    result = alembic(env, "upgrade", "head")
    assert result.returncode == 0, result.stderr
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO vehicles(id,vehicle_type,license_plate,brand,model,department,status) VALUES(:id,'forklift','PRIVATE-1234ABCD','B','M','Existing','ACTIVE')"
            ),
            {"id": uuid4()},
        )
    result = alembic(env, "downgrade", "57dd1f893b21")
    assert result.returncode != 0 and "empty vehicles table" in result.stderr
    with engine.begin() as c:
        assert c.execute(text("SELECT vehicle_type FROM vehicles")).scalar() == "forklift"
        assert snapshot(c)["departments"] == ["Existing"]
        cleanup(c, snapshot(c))
    result = alembic(env, "downgrade", "57dd1f893b21")
    assert result.returncode == 0, result.stderr


def test_cleanup_isolation(migration_db):
    engine, env = migration_db
    assert alembic(env, "upgrade", "57dd1f893b21").returncode == 0
    user, driver, vehicle, expense, maintenance, contract = [uuid4() for _ in range(6)]
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO users(id,email,username,full_name,hashed_password,role,is_active,language) VALUES(:id,'keep@test','keep','Keep','hash','ADMIN',true,'en')"
            ),
            {"id": user},
        )
        c.execute(
            text(
                "INSERT INTO drivers(id,full_name,employee_id,phone,license_number,license_category,license_expiry,medical_expiry,hire_date,status) VALUES(:id,'Keep','KEEP','123','KEEP','B','2030-01-01','2030-01-01','2020-01-01','ACTIVE')"
            ),
            {"id": driver},
        )
        c.execute(
            text(
                "INSERT INTO vehicles(id,license_plate,vin,brand,model,year,body_type,fuel_type,transmission,status,current_mileage,department) VALUES(:id,'OLD','12345678901234567','B','M',2026,'SEDAN','DIESEL','MANUAL','ACTIVE',0,' Retained name ' )"
            ),
            {"id": vehicle},
        )
        c.execute(
            text(
                "INSERT INTO expenses(id,vehicle_id,category,amount,currency,date) VALUES(:id,:vehicle,'OTHER',0.30,'EUR','2026-01-01')"
            ),
            {"id": expense, "vehicle": vehicle},
        )
        c.execute(
            text(
                "INSERT INTO maintenance_records(id,vehicle_id,type,title,status) VALUES(:id,:vehicle,'REPAIR','Demo','SCHEDULED')"
            ),
            {"id": maintenance, "vehicle": vehicle},
        )
        c.execute(
            text(
                "INSERT INTO contracts(id,vehicle_id,type,contractor,start_date,end_date,payment_frequency,status,auto_renew) VALUES(:id,:vehicle,'WARRANTY','Demo','2026-01-01','2027-01-01','ONE_TIME','ACTIVE',false)"
            ),
            {"id": contract, "vehicle": vehicle},
        )
        c.execute(
            text(
                "INSERT INTO mileage_logs(id,vehicle_id,recorded_by,value,source) VALUES(:id,:vehicle,:user,100,'MANUAL')"
            ),
            {"id": uuid4(), "vehicle": vehicle, "user": user},
        )
        for kind, entity in [
            ("vehicle", vehicle),
            ("expense", expense),
            ("maintenance", maintenance),
            ("contract", contract),
            ("driver", driver),
        ]:
            c.execute(
                text(
                    "INSERT INTO documents(id,entity_type,entity_id,type,filename,s3_key,mime_type,size_bytes) VALUES(:id,CAST(:kind AS entity_type),:entity,'OTHER','keep','keep','text/plain',1)"
                ),
                {"id": uuid4(), "kind": kind.upper(), "entity": entity},
            )
            c.execute(
                text(
                    "INSERT INTO notifications(id,user_id,title,message,type,is_read,entity_type,entity_id) VALUES(:id,:user,'Keep','Keep','SYSTEM',false,:kind,:entity)"
                ),
                {"id": uuid4(), "user": user, "kind": kind, "entity": entity},
            )
        before = snapshot(c)
        assert before["departments"] == ["Retained name"]
        assert snapshot(c) == before  # read-only and stable
        assert before["entities"]["expense"] == [str(expense)]
        assert before["entities"]["maintenance"] == [str(maintenance)]
        assert before["entities"]["contract"] == [str(contract)]
        cleanup(c, before)
        for table in ("expenses", "maintenance_records", "contracts", "mileage_logs"):
            assert c.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0
        assert c.execute(text("SELECT count(*) FROM vehicles")).scalar() == 0
        assert c.execute(text("SELECT count(*) FROM users")).scalar() == 1
        assert c.execute(text("SELECT count(*) FROM drivers")).scalar() == 1
        assert c.execute(text("SELECT entity_id FROM documents")).scalar() == driver
        assert c.execute(text("SELECT entity_id FROM notifications")).scalar() == driver
        cleanup(c, snapshot(c))


def test_concurrent_mileage_serializes(migration_db, monkeypatch):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import settings
    from app.models.vehicle import Vehicle
    from app.schemas.vehicle import VehicleCreate, VehicleUpdate
    from app.services.errors import DomainError
    from app.services.vehicle_service import VehicleService

    engine, env = migration_db
    assert alembic(env, "upgrade", "head").returncode == 0
    monkeypatch.setattr(settings, "VEHICLE_DEPARTMENTS", ["Test department"])

    async def scenario():
        async_engine = create_async_engine(env["DATABASE_URL"])
        sessions = async_sessionmaker(async_engine, expire_on_commit=False)
        async with sessions() as seed:
            vehicle = await VehicleService(seed).create(
                VehicleCreate(
                    vehicle_type="van",
                    license_plate="CONCURRENT",
                    brand="B",
                    model="M",
                    department="Test department",
                    current_mileage=100,
                )
            )
            vehicle_id = vehicle.id
            await seed.commit()
        async with sessions() as high:
            await VehicleService(high).update(vehicle_id, VehicleUpdate(current_mileage=200))
            started = asyncio.Event()

            async def low_reading():
                async with sessions() as low:
                    started.set()
                    with pytest.raises(DomainError, match="decreased"):
                        await VehicleService(low).update(vehicle_id, VehicleUpdate(current_mileage=150))
                    await low.rollback()

            task = asyncio.create_task(low_reading())
            await started.wait()
            await high.commit()
            await task
        async with sessions() as verify:
            assert (await verify.get(Vehicle, vehicle_id)).current_mileage == 200
        await async_engine.dispose()

    asyncio.run(scenario())


def test_readiness_cli_and_explicit_confirmation(migration_db, monkeypatch, capsys, tmp_path):
    import json

    from app.config import settings
    from scripts.vehicle_demo_cleanup import main

    engine, env = migration_db
    assert alembic(env, "upgrade", "head").returncode == 0
    monkeypatch.setattr(settings, "DATABASE_URL_SYNC", env["DATABASE_URL_SYNC"])
    monkeypatch.setattr("sys.argv", ["vehicle_demo_cleanup"])
    main()
    output = json.loads(capsys.readouterr().out)
    assert output == {
        "ready": True,
        "departments": [],
        "entities": {"vehicle": [], "expense": [], "maintenance": [], "compliance_document": []},
    }
    monkeypatch.setattr("sys.argv", ["vehicle_demo_cleanup", "--confirm-delete-vehicle-demo"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    capture = tmp_path / "capture.json"
    monkeypatch.setattr(
        "sys.argv", ["vehicle_demo_cleanup", "--confirm-delete-vehicle-demo", "--snapshot", str(capture)]
    )
    main()
    assert json.loads(capture.read_text()) == output
    with pytest.raises(FileExistsError):
        main()
    with engine.connect() as connection:
        assert snapshot(connection) == output
