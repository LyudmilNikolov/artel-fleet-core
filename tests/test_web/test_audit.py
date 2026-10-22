"""Audit log page and API/web actions that must appear in it."""

import datetime as dt
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.audit_log import AuditAction, AuditLog
from tests.conftest import auth_header
from tests.test_api.test_vehicles import VEHICLE_DATA

SOFIA = ZoneInfo("Europe/Sofia")


async def login(client, username="testadmin"):
    assert (await client.post("/login", data={"username": username, "password": "testpass123"})).status_code == 302


async def add_entry(db, when, action=AuditAction.CREATE, entity="vehicle", user_id=None, label="X"):
    db.add(
        AuditLog(
            user_id=user_id,
            action=action,
            entity_type=entity,
            entity_id=uuid4(),
            changes={"_label": label},
            timestamp=when,
        )
    )
    await db.flush()


@pytest.mark.asyncio
async def test_vehicle_created_through_the_api_is_audited_with_user_and_ip(
    client: AsyncClient, db_session, manager_token, fleet_manager_user
):
    created = await client.post("/api/v1/vehicles", json=VEHICLE_DATA, headers=auth_header(manager_token))
    assert created.status_code == 201
    rows = list((await db_session.scalars(select(AuditLog).where(AuditLog.entity_type == "vehicle"))).all())
    assert [(r.action, str(r.entity_id)) for r in rows] == [(AuditAction.CREATE, created.json()["id"])]
    assert rows[0].user_id == fleet_manager_user.id
    assert rows[0].ip_address and rows[0].changes["_label"].startswith("123 ABC 01")


@pytest.mark.asyncio
async def test_login_logout_and_export_are_audited(client: AsyncClient, db_session, admin_user):
    await db_session.commit()
    await login(client)
    assert (await client.get("/reports/export/tco.xlsx?vehicle_type=van")).status_code == 200
    await client.get("/logout")
    rows = list((await db_session.scalars(select(AuditLog).order_by(AuditLog.timestamp))).all())
    wanted = {AuditAction.LOGIN, AuditAction.EXPORT, AuditAction.LOGOUT}
    by_action = {r.action: r for r in rows if r.action in wanted}
    assert set(by_action) == wanted
    assert all(r.user_id == admin_user.id for r in by_action.values())
    assert by_action[AuditAction.EXPORT].changes == {"report": "tco_report", "format": "xlsx", "vehicle_type": "van"}
    assert by_action[AuditAction.LOGIN].entity_type == "session"


@pytest.mark.asyncio
async def test_audit_page_date_user_action_and_entity_filters(client: AsyncClient, db_session, admin_user):
    # Sofia wall-clock times: the filter days are Sofia calendar days (00:00 Sofia on the 6th is 22:00 UTC on the 5th)
    day = lambda d, h=12: dt.datetime(2026, 3, d, h, 0, tzinfo=SOFIA)  # noqa: E731
    await add_entry(db_session, day(1), label="first-day")
    await add_entry(db_session, day(5, 23), AuditAction.UPDATE, label="late-fifth")
    await add_entry(db_session, day(6, 0), AuditAction.DELETE, "expense", admin_user.id, label="start-of-sixth")
    await add_entry(db_session, day(9), label="ninth")
    await db_session.commit()
    await login(client)

    def labels(page):
        return {x for x in ("first-day", "late-fifth", "start-of-sixth", "ninth") if x in page.text}

    assert labels(await client.get("/settings/audit")) == {"first-day", "late-fifth", "start-of-sixth", "ninth"}
    # the end date is inclusive for the whole day
    page = await client.get("/settings/audit?start_date=2026-03-05&end_date=2026-03-05")
    assert labels(page) == {"late-fifth"}
    assert labels(await client.get("/settings/audit?start_date=2026-03-06")) == {"start-of-sixth", "ninth"}
    assert labels(await client.get("/settings/audit?end_date=2026-03-01")) == {"first-day"}
    assert labels(await client.get("/settings/audit?action=delete")) == {"start-of-sixth"}
    assert labels(await client.get("/settings/audit?entity_type=expense")) == {"start-of-sixth"}
    assert labels(await client.get(f"/settings/audit?user_id={admin_user.id}&start_date=2026-03-01")) == {
        "start-of-sixth"
    }
    # filters survive pagination links
    paged = await client.get("/settings/audit?start_date=2026-03-01&size=1")
    assert "start_date=2026-03-01" in paged.text and "page=2" in paged.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query", ["start_date=bad", "start_date=2026-05-01&end_date=2026-01-01", "user_id=zzz", "action=fly"]
)
async def test_audit_page_bad_filters_are_422(client: AsyncClient, db_session, admin_user, query):
    await db_session.commit()
    await login(client)
    response = await client.get(f"/settings/audit?{query}")
    assert response.status_code == 422 and 'role="alert"' in response.text


@pytest.mark.asyncio
async def test_audit_page_is_admin_only(client: AsyncClient, db_session, fleet_manager_user):
    await db_session.commit()
    await login(client, "testmanager")
    response = await client.get("/settings/audit", follow_redirects=False)
    assert response.status_code in (302, 303) and response.headers["location"] == "/"


@pytest.mark.asyncio
async def test_audit_page_shows_sofia_time_with_the_right_day_boundary(client: AsyncClient, db_session, admin_user):
    winter = dt.datetime(2026, 1, 15, 22, 30, 5, tzinfo=dt.UTC)  # 00:30:05 on the 16th in Sofia (UTC+2)
    summer = dt.datetime(2026, 7, 15, 21, 30, 5, tzinfo=dt.UTC)  # 00:30:05 on the 16th in Sofia (UTC+3)
    await add_entry(db_session, winter, label="winter-entry")
    await add_entry(db_session, summer, label="summer-entry")
    await db_session.commit()
    await login(client)

    bg = (await client.get("/settings/audit?lang=bg")).text
    assert "16.01.2026 00:30:05" in bg and "16.07.2026 00:30:05" in bg
    assert "15.01.2026 22:30" not in bg
    en = (await client.get("/settings/audit?lang=en")).text
    assert "16/01/2026 00:30:05" in en

    for day, label, other in (
        ("2026-01-16", "winter-entry", "2026-01-15"),
        ("2026-07-16", "summer-entry", "2026-07-15"),
    ):
        on_sofia_day = await client.get(f"/settings/audit?start_date={day}&end_date={day}")
        assert label in on_sofia_day.text
        on_utc_day = await client.get(f"/settings/audit?start_date={other}&end_date={other}")
        assert label not in on_utc_day.text
