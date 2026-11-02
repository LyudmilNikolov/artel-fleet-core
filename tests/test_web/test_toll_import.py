"""Toll import pages: upload -> preview -> import, managers only, problems stay visible."""

import re
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.expense import Expense, ExpenseCategory
from app.schemas.vehicle import VehicleCreate
from app.services.vehicle_service import VehicleService
from tests.test_services.test_toll_import import csv_bytes, line


async def login(client, username):
    assert (await client.post("/login", data={"username": username, "password": "testpass123"})).status_code == 302


async def upload(client, data, name="Priced_Transaction.csv"):
    return await client.post("/expenses/imports", files={"file": (name, data, "text/csv")})


@pytest.mark.asyncio
async def test_viewer_can_not_open_or_upload(client, db_session, viewer_user):
    await db_session.commit()
    await login(client, "testviewer")
    assert (await client.get("/expenses/imports")).status_code in (302, 303)
    response = await upload(client, csv_bytes(line("1", "CO7930CX")))
    assert response.status_code in (302, 303)
    assert await db_session.scalar(select(Expense).where(Expense.category == ExpenseCategory.TOLL)) is None


@pytest.mark.asyncio
async def test_upload_preview_then_import(client, db_session, fleet_manager_user):
    await VehicleService(db_session).create(
        VehicleCreate(
            vehicle_type="tractor_unit",
            license_plate="CO7930CX",
            brand="DAF",
            model="XF",
            department="Test department",
            current_mileage=1000,
        )
    )
    await db_session.commit()
    await login(client, "testmanager")

    listing = await client.get("/expenses")
    assert 'href="/expenses/imports"' in listing.text
    page = await client.get("/expenses/imports")
    assert page.status_code == 200 and 'name="file"' in page.text

    response = await upload(client, csv_bytes(line("1", "CO7930CX"), line("2", "XX1234YY", amount="3.00")))
    assert response.status_code == 303
    detail_url = response.headers["location"]
    preview = await client.get(detail_url)
    assert preview.status_code == 200
    assert "Nothing has been saved yet" in preview.text
    assert "XX1234YY" in preview.text and "Vehicle not in FleetCore" in preview.text
    assert "/vehicles/new?license_plate=XX1234YY" in preview.text
    assert await db_session.scalar(select(Expense).where(Expense.category == ExpenseCategory.TOLL)) is None

    applied = await client.post(detail_url + "/apply")
    assert applied.status_code == 303
    result = await client.get(detail_url)
    assert "Imported transactions: 1" in result.text and "Waiting" in result.text
    expense = await db_session.scalar(select(Expense).where(Expense.category == ExpenseCategory.TOLL))
    assert expense.amount == Decimal("10.51")

    shown = await client.get(f"/expenses/{expense.id}")
    assert "Imported card transactions (1)" in shown.text and "Not invoiced yet" in shown.text

    index = await client.get("/expenses/imports")
    assert "Waiting to be imported (1)" in index.text and "Priced_Transaction.csv" in index.text
    inbox = await client.get("/notifications", params={"tab": "messages"})
    open_link = re.search(r'href="(/notifications/[0-9a-f-]+/open)"', inbox.text).group(1)
    opened = await client.get(open_link, follow_redirects=False)  # marks the message read, then opens the import
    assert opened.status_code == 303 and opened.headers["location"] == detail_url

    again = await client.post(detail_url + "/apply")
    assert "already" in (await client.get(again.headers["location"])).text

    prefilled = await client.get("/vehicles/new", params={"license_plate": "XX1234YY"})
    assert 'value="XX1234YY"' in prefilled.text


@pytest.mark.asyncio
async def test_wrong_file_is_explained(client, db_session, fleet_manager_user):
    await db_session.commit()
    await login(client, "testmanager")
    toll_report = csv_bytes(header=["Име на обект", "Код на оператора за пътни такси"], encoding="utf-32")
    response = await upload(client, toll_report, "TollTransaction.csv")
    assert response.status_code == 400 and "Priced Transaction" in response.text
    response = await upload(client, b"%PDF-1.4 not a csv", "invoice.pdf")
    assert response.status_code == 400 and "Missing columns" in response.text


@pytest.mark.asyncio
async def test_newly_invoiced_transactions_are_listed(client, db_session, fleet_manager_user):
    await VehicleService(db_session).create(
        VehicleCreate(
            vehicle_type="tractor_unit",
            license_plate="CO7930CX",
            brand="DAF",
            model="XF",
            department="Test department",
            current_mileage=1000,
        )
    )
    await db_session.commit()
    await login(client, "testmanager")
    first = await upload(client, csv_bytes(line("1", "CO7930CX")))
    await client.post(first.headers["location"] + "/apply")

    again = await upload(client, csv_bytes(line("1", "CO7930CX", invoice="9400125555")))
    preview = (await client.get(again.headers["location"])).text
    assert "Newly invoiced (1)" in preview and "9400125555" in preview and "Not invoiced yet" in preview
    assert "Save the invoice numbers" in preview and "1 newly invoiced" in preview
    await client.post(again.headers["location"] + "/apply")
    done = (await client.get(again.headers["location"])).text
    assert "Invoice numbers filled in: 1." in done and "Newly invoiced (1)" in done
