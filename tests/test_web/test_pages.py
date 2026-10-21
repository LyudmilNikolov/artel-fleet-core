"""Both locales render the primary full pages and HTMX dashboard fragments."""

import pytest


@pytest.mark.asyncio
async def test_primary_pages_both_locales(client, admin_user, db_session):
    await db_session.commit()
    assert (
        await client.post("/login", data={"username": admin_user.username, "password": "testpass123"})
    ).status_code == 302
    paths = [
        "/",
        "/profile",
        "/vehicles",
        "/vehicles/new",
        "/drivers",
        "/drivers/new",
        "/expenses",
        "/expenses/new",
        "/maintenance",
        "/maintenance/new",
        "/compliance",
        "/compliance/new",
        "/notifications",
        "/reports",
        "/reports/tco",
        "/reports/fuel",
        "/reports/expenses",
        "/help",
        "/settings/users",
        "/settings/audit",
    ]
    widgets = [
        "/widgets/fleet-overview",
        "/widgets/attention-needed",
        "/widgets/expense-chart",
        "/widgets/maintenance-stats",
        "/widgets/top-vehicles",
        "/notifications/bell",
        "/notifications/dropdown",
    ]
    for lang in ["bg", "en"]:
        await client.get("/set-language/" + lang)
        for path in paths:
            response = await client.get(path)
            assert response.status_code == 200, (path, response.text[:200])
            assert f'<html lang="{lang}"' in response.text, path
        for path in widgets:
            response = await client.get(path, headers={"HX-Request": "true"})
            assert response.status_code == 200, path
