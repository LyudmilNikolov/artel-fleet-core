"""Fuel liters become NUMERIC(10,2): data is kept, and values that would be rounded block the upgrade."""

from uuid import uuid4

from sqlalchemy import text

from tests.test_migrations.test_postgres_eur import alembic, migration_db  # noqa: F401

# Imported pytest fixture is intentionally injected by parameter name.
# ruff: noqa: F811

PREVIOUS = "d1a8c6f2b974"


def add_fuel(c, vehicle, liters):
    expense = uuid4()
    c.execute(
        text(
            "INSERT INTO expenses(id,vehicle_id,category,amount,currency,date,fuel_liters) "
            "VALUES(:id,:vehicle,'FUEL',50,'EUR','2026-01-01',:liters)"
        ),
        {"id": expense, "vehicle": vehicle, "liters": liters},
    )
    return expense


def test_upgrade_refuses_values_it_would_round_then_keeps_data(migration_db):
    engine, env = migration_db
    assert alembic(env, "upgrade", PREVIOUS).returncode == 0
    vehicle = uuid4()
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO vehicles(id,vehicle_type,license_plate,brand,model,department,status) "
                "VALUES(:id,'van','CA1234AB','B','M','Dept','ACTIVE')"
            ),
            {"id": vehicle},
        )
        exact = add_fuel(c, vehicle, 80.5)
        long = add_fuel(c, vehicle, 40.123)
        add_fuel(c, vehicle, None)
    refused = alembic(env, "upgrade", "head")
    assert refused.returncode != 0 and "more than two decimals" in refused.stderr + refused.stdout
    with engine.begin() as c:
        c.execute(text("UPDATE expenses SET fuel_liters = 40.12 WHERE id = :id"), {"id": long})
    assert alembic(env, "upgrade", "head").returncode == 0
    with engine.connect() as c:
        column = c.execute(
            text(
                "SELECT numeric_precision, numeric_scale FROM information_schema.columns "
                "WHERE table_name = 'expenses' AND column_name = 'fuel_liters'"
            )
        ).one()
        assert (column.numeric_precision, column.numeric_scale) == (10, 2)
        values = c.execute(text("SELECT id, fuel_liters FROM expenses WHERE fuel_liters IS NOT NULL")).all()
        assert {str(i): str(v) for i, v in values} == {str(exact): "80.50", str(long): "40.12"}
    assert alembic(env, "check").returncode == 0
    assert alembic(env, "downgrade", PREVIOUS).returncode == 0
