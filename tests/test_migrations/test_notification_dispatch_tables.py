"""Dispatch state tables: structure, uniqueness (idempotency guard) and a clean downgrade."""

from uuid import uuid4

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from tests.test_migrations.test_postgres_eur import alembic, migration_db  # noqa: F401

# Imported pytest fixture is intentionally injected by parameter name.
# ruff: noqa: F811

PREVIOUS = "f3c7a9d2b815"
TABLES = {"notification_stops", "notification_deliveries", "notification_runs"}


def refused(engine, sql, **params):
    with pytest.raises(IntegrityError), engine.begin() as c:
        c.execute(text(sql), params)


def test_upgrade_creates_empty_tables_and_downgrade_removes_them(migration_db):
    engine, env = migration_db
    assert alembic(env, "upgrade", PREVIOUS).returncode == 0
    assert TABLES.isdisjoint(inspect(engine).get_table_names())
    result = alembic(env, "upgrade", "head")
    assert result.returncode == 0, result.stderr
    assert TABLES <= set(inspect(engine).get_table_names())
    with engine.connect() as c:
        assert all(c.execute(text(f"SELECT count(*) FROM {t}")).scalar() == 0 for t in TABLES)
    down = alembic(env, "downgrade", PREVIOUS)
    assert down.returncode == 0, down.stderr
    assert TABLES.isdisjoint(inspect(engine).get_table_names())


def test_uniqueness_checks_and_user_deletion(migration_db):
    engine, env = migration_db
    assert alembic(env, "upgrade", "head").returncode == 0
    doc, user = uuid4(), uuid4()
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO users(id,email,username,full_name,hashed_password,role,is_active,language) "
                "VALUES(:id,'u@test.com','u','Name','hash','VIEWER',true,'bg')"
            ),
            {"id": user},
        )
        c.execute(
            text(
                "INSERT INTO notification_stops(id,entity_type,entity_id,end_date,stopped_by) "
                "VALUES(:id,'compliance_document',:doc,'2026-12-01',:user)"
            ),
            {"id": uuid4(), "doc": doc, "user": user},
        )
        c.execute(
            text(
                "INSERT INTO notification_deliveries(id,entity_type,entity_id,end_date,channel,recipient,user_id,point,status) "
                "VALUES(:id,'compliance_document',:doc,'2026-12-01','email','u@test.com',:user,30,'sent')"
            ),
            {"id": uuid4(), "doc": doc, "user": user},
        )
        c.execute(
            text("INSERT INTO notification_runs(id,run_date,source) VALUES(:id,'2026-10-21','vehicle:casco')"),
            {"id": uuid4()},
        )

    # one active stop per document and end date; a resumed stop does not count, a new end date is separate
    stop = (
        "INSERT INTO notification_stops(id,entity_type,entity_id,end_date) VALUES(:id,'compliance_document',:doc,:end)"
    )
    refused(engine, stop, id=uuid4(), doc=doc, end="2026-12-01")
    with engine.begin() as c:
        c.execute(text(stop), {"id": uuid4(), "doc": doc, "end": "2026-12-09"})
        c.execute(text("UPDATE notification_stops SET resumed_at=now() WHERE end_date='2026-12-01'"))
        c.execute(text(stop), {"id": uuid4(), "doc": doc, "end": "2026-12-01"})

    delivery = (
        "INSERT INTO notification_deliveries(id,entity_type,entity_id,end_date,channel,recipient,point,status) "
        "VALUES(:id,'compliance_document',:doc,'2026-12-01',:channel,:recipient,:point,:status)"
    )
    refused(engine, delivery, id=uuid4(), doc=doc, channel="email", recipient="u@test.com", point=30, status="failed")
    refused(engine, delivery, id=uuid4(), doc=doc, channel="email", recipient="x@test.com", point=30, status="bogus")
    with engine.begin() as c:  # other point, channel or recipient are separate deliveries
        for channel, recipient, point in (
            ("email", "u@test.com", 15),
            ("telegram", "u@test.com", 30),
            ("email", "x@test.com", 30),
        ):
            c.execute(
                text(delivery),
                {
                    "id": uuid4(),
                    "doc": doc,
                    "channel": channel,
                    "recipient": recipient,
                    "point": point,
                    "status": "failed",
                },
            )

    refused(
        engine, "INSERT INTO notification_runs(id,run_date,source) VALUES(:id,'2026-10-21','vehicle:casco')", id=uuid4()
    )
    with engine.begin() as c:
        c.execute(
            text("INSERT INTO notification_runs(id,run_date,source) VALUES(:id,'2026-10-22','vehicle:casco')"),
            {"id": uuid4()},
        )
        c.execute(text("DELETE FROM users WHERE id=:id"), {"id": user})
        assert c.execute(text("SELECT count(*) FROM notification_deliveries WHERE user_id IS NULL")).scalar() >= 1
        assert c.execute(text("SELECT count(*) FROM notification_stops WHERE stopped_by IS NULL")).scalar() >= 1
