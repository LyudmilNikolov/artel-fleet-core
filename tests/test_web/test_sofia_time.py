"""Every timestamp shown in the UI is in Sofia time (storage stays UTC)."""

import datetime as dt
from uuid import uuid4

import pytest
from sqlalchemy import update

from app.models.mileage import MileageLog
from app.models.user import User
from tests.test_web.test_notifications import login, vehicle


@pytest.mark.asyncio
async def test_users_page_shows_creation_time_in_sofia_time(client, db_session, admin_user):
    await db_session.execute(
        update(User).where(User.id == admin_user.id).values(created_at=dt.datetime(2026, 7, 15, 21, 30, tzinfo=dt.UTC))
    )
    await db_session.commit()
    await login(client, "testadmin")
    assert "16.07.2026 00:30" in (await client.get("/settings/users?lang=bg")).text
    assert "16/07/2026 00:30" in (await client.get("/settings/users?lang=en")).text


@pytest.mark.asyncio
async def test_vehicle_mileage_tab_shows_reading_time_in_sofia_time(client, db_session, admin_user):
    v = await vehicle(db_session, "CA1234SF")
    db_session.add(
        MileageLog(
            id=uuid4(),
            vehicle_id=v.id,
            recorded_by=admin_user.id,
            value=1000,
            recorded_at=dt.datetime(2026, 1, 15, 22, 30, tzinfo=dt.UTC),
        )
    )
    await db_session.commit()
    await login(client, "testadmin")
    tab = await client.get(f"/vehicles/{v.id}/tab/mileage?lang=bg")
    assert tab.status_code == 200 and "16.01.2026 00:30" in tab.text and "15.01.2026" not in tab.text
