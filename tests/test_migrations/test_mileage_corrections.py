"""Mileage correction columns: upgrade keeps data, downgrade refuses while corrections exist."""

from uuid import uuid4

from sqlalchemy import text

from tests.test_migrations.test_postgres_eur import alembic, migration_db  # noqa: F401

# Imported pytest fixture is intentionally injected by parameter name.
# ruff: noqa: F811

PREVIOUS = "8a3c51d0e7f2"


def insert_log(c, vehicle, value, correction=None):
    columns = "id,vehicle_id,value,source" + (",is_correction" if correction is not None else "")
    values = ":id,:vehicle,:value,'MANUAL'" + (",:correction" if correction is not None else "")
    c.execute(
        text(f"INSERT INTO mileage_logs({columns}) VALUES({values})"),
        {"id": uuid4(), "vehicle": vehicle, "value": value, "correction": correction},
    )


def test_upgrade_keeps_readings_and_downgrade_guard(migration_db):
    engine, env = migration_db
    assert alembic(env, "upgrade", PREVIOUS).returncode == 0
    vehicle = uuid4()
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO vehicles(id,vehicle_type,license_plate,brand,model,department,status,current_mileage) VALUES(:id,'van','CA1234AB','B','M','Dept','ACTIVE',500)"
            ),
            {"id": vehicle},
        )
        insert_log(c, vehicle, 100)
        insert_log(c, vehicle, 500)
    assert alembic(env, "upgrade", "head").returncode == 0
    with engine.begin() as c:
        rows = c.execute(text("SELECT value,is_correction,seq FROM mileage_logs ORDER BY seq")).all()
        assert [(r.value, r.is_correction) for r in rows] == [(100, False), (500, False)]
        assert rows[0].seq < rows[1].seq
        insert_log(c, vehicle, 300, correction=True)
    assert alembic(env, "check").returncode == 0
    result = alembic(env, "downgrade", PREVIOUS)
    assert result.returncode != 0 and "correction mileage readings exist" in result.stderr
    with engine.begin() as c:
        assert c.execute(text("SELECT count(*) FROM mileage_logs WHERE is_correction")).scalar() == 1
        c.execute(text("DELETE FROM mileage_logs WHERE is_correction"))
    result = alembic(env, "downgrade", PREVIOUS)
    assert result.returncode == 0, result.stderr
    with engine.begin() as c:
        columns = (
            c.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='mileage_logs'"))
            .scalars()
            .all()
        )
        assert "seq" not in columns and "is_correction" not in columns
        assert c.execute(text("SELECT count(*) FROM mileage_logs")).scalar() == 2
