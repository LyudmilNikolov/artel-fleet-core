"""Vehicle filters on the expense and maintenance lists, maintenance status / when filters, dashboard links."""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.config import settings
from app.schemas.expense import ExpenseCreate
from app.schemas.maintenance import MaintenanceCreate
from app.schemas.vehicle import VehicleCreate
from app.services.expense_service import ExpenseService
from app.services.maintenance_service import MaintenanceService
from app.services.validity import local_today
from app.services.vehicle_service import VehicleService

TODAY = local_today()
HX = {"HX-Request": "true"}


async def vehicle(db, kind, plate, brand="Volvo", model="FH"):
    return await VehicleService(db).create(
        VehicleCreate(
            vehicle_type=kind, license_plate=plate, brand=brand, model=model, department="Test department"
        )
    )


async def login(client, username):
    assert (await client.post("/login", data={"username": username, "password": "testpass123"})).status_code == 302


async def expense(db, v, category="parking", amount="10.00", vendor="Shop"):
    return await ExpenseService(db).create(
        ExpenseCreate(vehicle_id=v.id, category=category, amount=Decimal(amount), date=TODAY, vendor=vendor), None
    )


async def record(db, v, title, status="scheduled", days=None, **kw):
    return await MaintenanceService(db).create(
        MaintenanceCreate(
            vehicle_id=v.id,
            type="repair",
            title=title,
            status=status,
            scheduled_date=None if days is None else TODAY + timedelta(days=days),
            **kw,
        ),
        None,
    )


@pytest.fixture
async def fleet(db_session):
    return {
        "tractor": await vehicle(db_session, "tractor_unit", "CA1111AA", "Volvo", "FH16"),
        "van": await vehicle(db_session, "van", "CB2222BB", "Iveco", "Daily"),
        "container": await vehicle(db_session, "container_semi_trailer", "CC3333CC", "Schmitz", "Box"),
        "curtain": await vehicle(db_session, "curtain_side_semi_trailer", "CT4444DD", "Krone", "Curtain"),
    }


@pytest.mark.asyncio
async def test_expense_list_filters_by_vehicle(client, db_session, fleet, fleet_manager_user):
    await expense(db_session, fleet["tractor"], vendor="Tractor vendor")
    await expense(db_session, fleet["van"], vendor="Van vendor", category="washing")
    await expense(db_session, fleet["container"], vendor="Container vendor")
    await expense(db_session, fleet["curtain"], vendor="Curtain vendor")
    await login(client, "testmanager")

    everything = (await client.get("/expenses")).text
    assert all(w in everything for w in ("Tractor vendor", "Van vendor", "Container vendor", "Curtain vendor"))
    assert 'id="expense-search"' in everything and 'id="expense-type"' in everything and 'hx-target="#expense-results"' in everything

    by_plate = (await client.get("/expenses", params={"q": "ca 1111"})).text  # plates match with or without spaces
    assert "Tractor vendor" in by_plate and "Van vendor" not in by_plate
    by_brand = (await client.get("/expenses", params={"q": "iveco"})).text
    assert "Van vendor" in by_brand and "Tractor vendor" not in by_brand
    by_type = (await client.get("/expenses", params={"vehicle_type": "van"})).text
    assert "Van vendor" in by_type and "Tractor vendor" not in by_type
    trailers = (await client.get("/expenses", params={"vehicle_type": "trailer"})).text
    assert "Container vendor" in trailers and "Curtain vendor" in trailers and "Van vendor" not in trailers
    combined = (await client.get("/expenses", params={"vehicle_type": "van", "category": "parking"})).text
    assert "Van vendor" not in combined  # the van's expense is a car wash
    assert "No data available" in (await client.get("/expenses", params={"q": "nomatch"})).text
    assert "Tractor vendor" in (await client.get("/expenses", params={"vehicle_type": "bogus"})).text  # ignored

    partial = await client.get("/expenses", params={"q": "volvo"}, headers={**HX, "HX-Target": "expense-results"})
    assert "<html" not in partial.text and "Tractor vendor" in partial.text and 'id="expense-search"' not in partial.text


@pytest.mark.asyncio
async def test_expense_pagination_keeps_the_filters(client, db_session, fleet, fleet_manager_user):
    for i in range(3):
        await expense(db_session, fleet["van"], vendor=f"Van vendor {i}")
    await expense(db_session, fleet["tractor"], vendor="Tractor vendor")
    await login(client, "testmanager")
    page = (await client.get("/expenses", params={"vehicle_type": "van", "size": 2})).text
    assert "page=2" in page and "vehicle_type=van" in page and "Tractor vendor" not in page
    assert "Van vendor" in (await client.get("/expenses", params={"vehicle_type": "van", "size": 2, "page": 2})).text


@pytest.mark.asyncio
async def test_maintenance_list_vehicle_filters(client, db_session, fleet, fleet_manager_user):
    await record(db_session, fleet["tractor"], "Tractor job")
    await record(db_session, fleet["van"], "Van job")
    await record(db_session, fleet["curtain"], "Curtain job")
    await login(client, "testmanager")
    page = (await client.get("/maintenance")).text
    assert all(w in page for w in ("Tractor job", "Van job", "Curtain job"))
    assert 'id="maintenance-search"' in page and 'id="maintenance-type"' in page and 'hx-target="#maintenance-results"' in page

    by_q = (await client.get("/maintenance", params={"q": "CB2222"})).text
    assert "Van job" in by_q and "Tractor job" not in by_q
    by_type = (await client.get("/maintenance", params={"vehicle_type": "trailer"})).text
    assert "Curtain job" in by_type and "Van job" not in by_type
    partial = await client.get("/maintenance", params={"q": "FH16"}, headers={**HX, "HX-Target": "maintenance-results"})
    assert "<html" not in partial.text and "Tractor job" in partial.text and "Van job" not in partial.text


@pytest.mark.asyncio
async def test_maintenance_status_and_when_filters(client, db_session, fleet, fleet_manager_user):
    v = fleet["van"]
    soon = settings.MAINTENANCE_UPCOMING_DAYS
    await record(db_session, v, "Late one", days=-3)
    await record(db_session, v, "Tomorrow one", days=1)
    await record(db_session, v, "Edge one", days=soon)
    await record(db_session, v, "Far one", days=soon + 1)
    await record(db_session, v, "Working one", status="in_progress", days=-1)
    await record(db_session, v, "Done one", status="completed", days=-20)
    await record(db_session, v, "Undated one")
    await login(client, "testmanager")

    def titles(text):
        """Titles present on the page, in the order they appear."""
        found = [t for t in ("Late one", "Tomorrow one", "Edge one", "Far one", "Working one", "Done one", "Undated one") if t in text]
        return sorted(found, key=text.index)

    scheduled = (await client.get("/maintenance", params={"status": "scheduled"})).text
    assert titles(scheduled) == ["Late one", "Tomorrow one", "Edge one", "Far one", "Undated one"]  # soonest first
    assert titles((await client.get("/maintenance", params={"status": "in_progress"})).text) == ["Working one"]
    assert titles((await client.get("/maintenance", params={"status": "completed"})).text) == ["Done one"]
    pending = (await client.get("/maintenance", params={"status": "pending"})).text
    assert titles(pending) == ["Late one", "Working one", "Tomorrow one", "Edge one", "Far one", "Undated one"]

    overdue = (await client.get("/maintenance", params={"status": "scheduled", "when": "overdue"})).text
    assert titles(overdue) == ["Late one"]  # in-progress and undated records are not overdue
    upcoming = (await client.get("/maintenance", params={"status": "scheduled", "when": "upcoming"})).text
    assert titles(upcoming) == ["Tomorrow one", "Edge one"]  # inside the window, window end included
    assert titles((await client.get("/maintenance", params={"when": "upcoming"})).text) == ["Tomorrow one", "Edge one"]
    assert titles((await client.get("/maintenance", params={"status": "completed", "when": "overdue"})).text) == []
    assert "Late one" in (await client.get("/maintenance", params={"status": "bogus", "when": "bogus"})).text  # ignored

    # the filter bar shows the chosen values and the options
    page = (await client.get("/maintenance", params={"status": "pending", "when": "upcoming"})).text
    assert 'status: "pending"' in page.replace("&#34;", '"') and 'when: "upcoming"' in page.replace("&#34;", '"')
    assert "Pending (scheduled and in progress)" in page and f"Upcoming (next {soon} days)" in page and "Reset" in page


@pytest.mark.asyncio
async def test_dashboard_links_open_the_matching_lists(client, db_session, fleet, fleet_manager_user):
    v = fleet["van"]
    await record(db_session, v, "Late one", days=-3)
    await record(db_session, v, "Tomorrow one", days=1)
    await record(db_session, v, "Far one", days=settings.MAINTENANCE_UPCOMING_DAYS + 5)
    await login(client, "testmanager")
    widget = (await client.get("/widgets/attention-needed")).text
    assert 'href="/maintenance?status=scheduled&when=overdue"' in widget
    assert 'href="/maintenance?status=scheduled&when=upcoming"' in widget

    # the counts on the dashboard are exactly the rows behind the links
    from app.services.dashboard_service import DashboardService

    stats = await DashboardService(db_session).attention_needed()
    overdue = (await client.get("/maintenance", params={"status": "scheduled", "when": "overdue"})).text
    upcoming = (await client.get("/maintenance", params={"status": "scheduled", "when": "upcoming"})).text
    assert (stats["overdue_maintenance"], stats["upcoming_maintenance"]) == (1, 1)
    assert "Late one" in overdue and "Tomorrow one" not in overdue
    assert "Tomorrow one" in upcoming and "Far one" not in upcoming and "Late one" not in upcoming

    stats_widget = (await client.get("/widgets/maintenance-stats")).text
    for status in ("scheduled", "in_progress", "completed"):
        assert f'href="/maintenance?status={status}"' in stats_widget


@pytest.mark.asyncio
async def test_complete_button_returns_to_the_filtered_list(client, db_session, fleet, fleet_manager_user):
    rec = await record(db_session, fleet["van"], "Late one", days=-3)
    await login(client, "testmanager")
    page = (await client.get("/maintenance", params={"status": "scheduled", "when": "overdue"})).text
    assert 'name="next" value="/maintenance?status=scheduled&amp;when=overdue"' in page or 'name="next" value="/maintenance?status=scheduled&when=overdue"' in page
    done = await client.post(f"/maintenance/{rec.id}/complete", data={"next": "/maintenance?status=scheduled&when=overdue"})
    assert done.status_code == 303 and done.headers["location"] == "/maintenance?status=scheduled&when=overdue"


@pytest.mark.asyncio
async def test_fleet_overview_cards_open_the_vehicle_list_with_that_status(client, db_session, fleet, fleet_manager_user):
    await login(client, "testmanager")
    widget = (await client.get("/widgets/fleet-overview")).text
    for href in ("/vehicles", "/vehicles?vehicle_type=trailer", "/vehicles?status=active", "/vehicles?status=in_maintenance", "/vehicles?status=decommissioned"):
        assert f'href="{href}"' in widget
    # the target filter is applied by the vehicle list
    page = (await client.get("/vehicles", params={"status": "active"})).text.replace("&#34;", '"')
    assert 'status: "active"' in page


@pytest.mark.asyncio
async def test_expense_list_filters_by_date_range(client, db_session, fleet, fleet_manager_user):
    service = ExpenseService(db_session)
    for days_ago, vendor, category in ((40, "Old vendor", "parking"), (10, "Mid vendor", "washing"), (0, "New vendor", "parking")):
        await service.create(
            ExpenseCreate(
                vehicle_id=fleet["van"].id,
                category=category,
                amount=Decimal("10.00"),
                date=TODAY - timedelta(days=days_ago),
                vendor=vendor,
            ),
            None,
        )
    await login(client, "testmanager")
    day = lambda n: (TODAY - timedelta(days=n)).isoformat()  # noqa: E731

    page = (await client.get("/expenses")).text
    assert 'id="expense-date-from"' in page and 'id="expense-date-to"' in page

    both = (await client.get("/expenses", params={"date_from": day(10), "date_to": day(10)})).text
    assert "Mid vendor" in both and "Old vendor" not in both and "New vendor" not in both  # both ends inclusive

    since = (await client.get("/expenses", params={"date_from": day(10)})).text
    assert "Mid vendor" in since and "New vendor" in since and "Old vendor" not in since

    until = (await client.get("/expenses", params={"date_to": day(10)})).text
    assert "Old vendor" in until and "Mid vendor" in until and "New vendor" not in until

    reversed_range = (await client.get("/expenses", params={"date_from": day(0), "date_to": day(10)})).text
    assert "Mid vendor" in reversed_range and "New vendor" in reversed_range and "Old vendor" not in reversed_range

    with_category = (await client.get("/expenses", params={"date_from": day(40), "category": "parking"})).text
    assert "Old vendor" in with_category and "New vendor" in with_category and "Mid vendor" not in with_category

    ignored = (await client.get("/expenses", params={"date_from": "", "date_to": "not-a-date"}))
    assert ignored.status_code == 200 and "Old vendor" in ignored.text and "New vendor" in ignored.text
    assert 'value="' + day(10) + '"' not in ignored.text

    kept = (await client.get("/expenses", params={"date_from": day(10)})).text
    assert day(10) in kept and "btn" in kept  # the chosen date stays in the form after reload


@pytest.mark.asyncio
async def test_expense_pagination_keeps_the_date_filter(client, db_session, fleet, fleet_manager_user):
    for i in range(3):
        await expense(db_session, fleet["van"], vendor=f"Van vendor {i}")
    await login(client, "testmanager")
    page = (await client.get("/expenses", params={"date_from": TODAY.isoformat(), "size": 2})).text
    assert "page=2" in page and f"date_from={TODAY.isoformat()}" in page


@pytest.mark.asyncio
async def test_top_expensive_vehicle_links_to_its_filtered_expenses(client, db_session, fleet, fleet_manager_user):
    await expense(db_session, fleet["tractor"], amount="500.00", vendor="Tractor vendor")
    await expense(db_session, fleet["van"], vendor="Van vendor")
    await login(client, "testmanager")
    widget = (await client.get("/widgets/top-vehicles")).text
    assert 'href="/expenses?q=CA1111AA"' in widget and f'href="/vehicles/{fleet["tractor"].id}"' not in widget
    listed = (await client.get("/expenses", params={"q": "CA1111AA"})).text
    assert "Tractor vendor" in listed and "Van vendor" not in listed


@pytest.mark.asyncio
async def test_driver_list_filters_apply_as_you_change_them(client, db_session, fleet_manager_user):
    """QA #32: the drivers list behaves like vehicles and expenses (HTMX results, Filter button kept as fallback)."""
    from tests.test_web.test_driver_access import make_driver

    await make_driver(db_session, "Findable Fiona")
    await make_driver(db_session, "Other Oscar")
    await db_session.commit()
    await login(client, "testmanager")
    page = (await client.get("/drivers")).text
    assert 'hx-get="/drivers"' in page and 'hx-target="#driver-results"' in page and 'id="driver-search"' in page
    assert 'type="submit"' in page  # without JavaScript the button still filters
    assert "Findable Fiona" in page and "Other Oscar" in page
    partial = await client.get("/drivers", params={"q": "Fiona"}, headers={**HX, "HX-Target": "driver-results"})
    assert "<html" not in partial.text and "Findable Fiona" in partial.text and "Other Oscar" not in partial.text
    assert 'id="driver-search"' not in partial.text
    plain = (await client.get("/drivers", params={"q": "Fiona"})).text
    assert "<html" in plain and "Findable Fiona" in plain and "Other Oscar" not in plain
    assert 'value="Fiona"' in plain
