"""service_item column: existing records are kept, downgrade removes only the column."""

from uuid import uuid4

from sqlalchemy import text

from tests.test_migrations.test_postgres_eur import alembic, migration_db  # noqa: F401

# ruff: noqa: F811

PREVIOUS = "e3b9c4a71f26"
HEAD = "f4c8d2e91a57"


def columns(c):
    return set(
        c.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'maintenance_records'")).scalars()
    )


def test_upgrade_keeps_records_and_downgrade_drops_the_column(migration_db):
    engine, env = migration_db
    assert alembic(env, "upgrade", PREVIOUS).returncode == 0
    vehicle, record = uuid4(), uuid4()
    with engine.begin() as c:
        assert "service_item" not in columns(c)
        c.execute(
            text(
                "INSERT INTO vehicles(id,vehicle_type,license_plate,brand,model,department,status) VALUES(:id,'van','CA1234AB','B','M','Dept','ACTIVE')"
            ),
            {"id": vehicle},
        )
        c.execute(
            text(
                "INSERT INTO maintenance_records(id,vehicle_id,type,title,status,next_service_mileage) VALUES(:id,:v,'SCHEDULED_SERVICE','Oil','COMPLETED',60000)"
            ),
            {"id": record, "v": vehicle},
        )
    assert alembic(env, "upgrade", HEAD).returncode == 0
    with engine.connect() as c:
        row = c.execute(text("SELECT service_item, next_service_mileage, title FROM maintenance_records")).one()
        assert tuple(row) == (None, 60000, "Oil")
    assert alembic(env, "downgrade", PREVIOUS).returncode == 0
    with engine.connect() as c:
        assert "service_item" not in columns(c)
        assert c.execute(text("SELECT count(*) FROM maintenance_records")).scalar() == 1
    assert alembic(env, "upgrade", "head").returncode == 0
    assert alembic(env, "check").returncode == 0


CHECKLIST_REVISION = "a1d7e5b3c902"


def test_filters_become_checklist_entries_and_downgrade_is_guarded(migration_db):
    engine, env = migration_db
    assert alembic(env, "upgrade", HEAD).returncode == 0
    vehicle = uuid4()
    ids = {name: uuid4() for name in ("oil_filter", "air_filter", "fuel_filter", "gearbox_oil", "none")}
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO vehicles(id,vehicle_type,license_plate,brand,model,department,status) VALUES(:id,'van','CA1234AB','B','M','Dept','ACTIVE')"
            ),
            {"id": vehicle},
        )
        for name, rid in ids.items():
            c.execute(
                text(
                    "INSERT INTO maintenance_records(id,vehicle_id,type,title,status,service_item) VALUES(:id,:v,'SCHEDULED_SERVICE','T','COMPLETED',:item)"
                ),
                {"id": rid, "v": vehicle, "item": None if name == "none" else name},
            )
    assert alembic(env, "upgrade", CHECKLIST_REVISION).returncode == 0
    with engine.connect() as c:
        rows = {
            r[0]: (r[1], r[2])
            for r in c.execute(text("SELECT id, service_item, service_checklist FROM maintenance_records")).all()
        }
    for name in ("oil_filter", "air_filter", "fuel_filter"):
        assert rows[ids[name]] == ("engine_oil", [name])
    assert rows[ids["gearbox_oil"]] == ("gearbox_oil", None) and rows[ids["none"]] == (None, None)

    refused = alembic(env, "downgrade", HEAD)
    assert refused.returncode != 0 and "checklist" in refused.stderr
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM maintenance_records WHERE service_checklist IS NOT NULL")).scalar() == 3
    confirmed = alembic(env, "-x", "drop_service_checklist=yes", "downgrade", HEAD)
    assert confirmed.returncode == 0, confirmed.stderr
    with engine.connect() as c:
        assert "service_checklist" not in columns(c)
    assert alembic(env, "upgrade", "head").returncode == 0
