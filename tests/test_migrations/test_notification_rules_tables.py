"""Reminder configuration tables: structure, constraints, cascades and a clean downgrade."""

from uuid import uuid4

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from tests.test_migrations.test_postgres_eur import alembic, migration_db  # noqa: F401

# Imported pytest fixture is intentionally injected by parameter name.
# ruff: noqa: F811

PREVIOUS = "e8b2f4a6c1d3"
TABLES = {"notification_settings", "notification_rules", "notification_rule_recipients"}


def add_user(c):
    user = uuid4()
    c.execute(
        text(
            "INSERT INTO users(id,email,username,full_name,hashed_password,role,is_active,language) "
            "VALUES(:id,:email,:username,'Name','hash','VIEWER',true,'bg')"
        ),
        {"id": user, "email": f"{user}@test.com", "username": str(user)[:20]},
    )
    return user


def add_rule(c, source="vehicle:casco", milestones="{30,15,10}"):
    rule = uuid4()
    c.execute(
        text("INSERT INTO notification_rules(id,source,milestone_days) VALUES(:id,:source,:milestones)"),
        {"id": rule, "source": source, "milestones": milestones},
    )
    return rule


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


def test_constraints_and_cascades(migration_db):
    engine, env = migration_db
    assert alembic(env, "upgrade", "head").returncode == 0

    with engine.begin() as c:
        user = add_user(c)
        rule = add_rule(c)
        c.execute(
            text("INSERT INTO notification_rule_recipients(id,rule_id,kind,user_id) VALUES(:id,:rule,'user',:user)"),
            {"id": uuid4(), "rule": rule, "user": user},
        )
        c.execute(
            text(
                "INSERT INTO notification_rule_recipients(id,rule_id,kind,address) VALUES(:id,:rule,'external_email','a@b.c')"
            ),
            {"id": uuid4(), "rule": rule},
        )
        # an extensible kind without target columns is allowed (for example the vehicle's driver, later)
        c.execute(
            text("INSERT INTO notification_rule_recipients(id,rule_id,kind) VALUES(:id,:rule,'vehicle_driver')"),
            {"id": uuid4(), "rule": rule},
        )

    def refused(sql, **params):
        with pytest.raises(IntegrityError), engine.begin() as c:
            c.execute(text(sql), params)

    refused("INSERT INTO notification_rules(id,source,milestone_days) VALUES(:id,'vehicle:casco','{5}')", id=uuid4())
    refused("INSERT INTO notification_rules(id,source,milestone_days) VALUES(:id,'a','{}')", id=uuid4())
    refused(
        "INSERT INTO notification_rules(id,source,milestone_days) VALUES(:id,'b','{1,2,3,4,5,6,7,8,9,10,11}')",
        id=uuid4(),
    )
    refused(
        "INSERT INTO notification_rules(id,source,milestone_days,repeat_every_days) VALUES(:id,'c','{5}',0)", id=uuid4()
    )
    refused(
        "INSERT INTO notification_rules(id,source,milestone_days,stop_after_overdue_days) VALUES(:id,'d','{5}',0)",
        id=uuid4(),
    )
    with engine.connect() as c:
        rule = c.execute(text("SELECT id FROM notification_rules")).scalar()
        user = c.execute(text("SELECT user_id FROM notification_rule_recipients WHERE kind='user'")).scalar()
    refused(
        "INSERT INTO notification_rule_recipients(id,rule_id,kind,user_id) VALUES(:id,:rule,'user',:user)",
        id=uuid4(),
        rule=rule,
        user=user,
    )  # same user twice
    refused(
        "INSERT INTO notification_rule_recipients(id,rule_id,kind,address) VALUES(:id,:rule,'external_email','a@b.c')",
        id=uuid4(),
        rule=rule,
    )  # same address twice
    refused("INSERT INTO notification_rule_recipients(id,rule_id,kind) VALUES(:id,:rule,'user')", id=uuid4(), rule=rule)
    refused("INSERT INTO notification_rule_recipients(id,rule_id,kind) VALUES(:id,:rule,'role')", id=uuid4(), rule=rule)
    refused(
        "INSERT INTO notification_rule_recipients(id,rule_id,kind) VALUES(:id,:rule,'external_telegram')",
        id=uuid4(),
        rule=rule,
    )

    # one settings row only
    with engine.begin() as c:
        c.execute(text("INSERT INTO notification_settings(id) VALUES(:id)"), {"id": uuid4()})
        row = c.execute(text("SELECT send_time, timezone FROM notification_settings")).one()
    assert (str(row[0]), row[1]) == ("08:00:00", "Europe/Sofia")
    refused("INSERT INTO notification_settings(id) VALUES(:id)", id=uuid4())
    refused("INSERT INTO notification_settings(id,singleton) VALUES(:id,false)", id=uuid4())

    # deleting a user removes them from rules; deleting a rule removes its recipients
    with engine.begin() as c:
        c.execute(text("DELETE FROM users WHERE id=:id"), {"id": user})
        assert c.execute(text("SELECT count(*) FROM notification_rule_recipients WHERE kind='user'")).scalar() == 0
        c.execute(text("DELETE FROM notification_rules WHERE id=:id"), {"id": rule})
        assert c.execute(text("SELECT count(*) FROM notification_rule_recipients")).scalar() == 0
