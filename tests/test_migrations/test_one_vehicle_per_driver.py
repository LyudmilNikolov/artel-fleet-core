"""One driver, one vehicle: the unique index is added, and the upgrade refuses data that would break it."""

from uuid import uuid4

from sqlalchemy import text

from tests.test_migrations.test_postgres_eur import alembic, migration_db  # noqa: F401

# ruff: noqa: F811

PREVIOUS = "c9f3a5e7b246"
HEAD = "d1a8c6f2b974"


def indexes(c):
    return set(c.execute(text("SELECT indexname FROM pg_indexes WHERE tablename = 'vehicles'")).scalars())


def add_vehicle(c, plate, driver_id=None):
    vehicle = uuid4()
    c.execute(
        text(
            "INSERT INTO vehicles(id,vehicle_type,license_plate,brand,model,department,status,assigned_driver_id) "
            "VALUES(:id,'van',:plate,'B','M','Dept','ACTIVE',:driver)"
        ),
        {"id": vehicle, "plate": plate, "driver": driver_id},
    )
    return vehicle


def test_upgrade_refuses_drivers_with_two_vehicles_then_adds_the_index(migration_db):
    engine, env = migration_db
    assert alembic(env, "upgrade", PREVIOUS).returncode == 0
    driver = uuid4()
    with engine.begin() as c:
        c.execute(text("INSERT INTO drivers(id,full_name,status) VALUES(:id,'Double Driver','ACTIVE')"), {"id": driver})
        first = add_vehicle(c, "CA1111AA", driver)
        second = add_vehicle(c, "CA2222BB", driver)
    refused = alembic(env, "upgrade", HEAD)
    assert refused.returncode != 0
    assert "Double Driver" in (refused.stderr + refused.stdout) and "more than one vehicle" in (
        refused.stderr + refused.stdout
    )
    with engine.connect() as c:
        assert "uq_vehicles_assigned_driver" not in indexes(c)
        assert (
            c.execute(text("SELECT count(*) FROM vehicles WHERE assigned_driver_id IS NOT NULL")).scalar() == 2
        )  # untouched
    # after the extra assignment is removed the upgrade goes through and the index is enforced
    with engine.begin() as c:
        c.execute(text("UPDATE vehicles SET assigned_driver_id = NULL WHERE id = :id"), {"id": second})
    assert alembic(env, "upgrade", HEAD).returncode == 0
    with engine.connect() as c:
        assert "uq_vehicles_assigned_driver" in indexes(c)
    assert alembic(env, "upgrade", "head").returncode == 0
    assert alembic(env, "check").returncode == 0
    assert alembic(env, "downgrade", PREVIOUS).returncode == 0
    with engine.connect() as c:
        assert "uq_vehicles_assigned_driver" not in indexes(c)
        assert c.execute(text("SELECT count(*) FROM vehicles WHERE assigned_driver_id IS NOT NULL")).scalar() == 1
    assert first is not None
