"""Driver logins: linked to a driver profile; without vehicles they see only that profile, with vehicles only those."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.compliance import ComplianceDocument
from app.models.driver import Driver, DriverStatus
from app.models.expense import Expense, ExpenseCategory
from app.models.maintenance import MaintenanceRecord, MaintenanceStatus, MaintenanceType
from app.models.user import User, UserRole
from app.schemas.vehicle import VehicleCreate
from app.services.driver_scope import DriverScope, driver_may_call_api, driver_may_open, resolve_scope
from app.services.user_service import UserService
from app.services.vehicle_service import VehicleService
from app.utils.security import create_access_token


async def make_vehicle(db, plate, driver=None):
    vehicle = await VehicleService(db).create(
        VehicleCreate(
            vehicle_type="tractor_unit",
            license_plate=plate,
            brand="Volvo",
            model="FH",
            department="Test department",
            current_mileage=1000,
        )
    )
    if driver is not None:
        vehicle.assigned_driver_id = driver.id
        await db.flush()
    return vehicle


async def make_driver(db, name="Ivan Driver", status=DriverStatus.ACTIVE):
    driver = Driver(id=uuid4(), full_name=name, status=status)
    db.add(driver)
    await db.flush()
    return driver


async def make_driver_login(db, driver, username="ivan"):
    user = await UserService(db).create(
        email=f"{username}@example.com",
        username=username,
        full_name=driver.full_name,
        password="driver-pass-1",
        role=UserRole.DRIVER,
        language="en",
        driver_id=driver.id,
    )
    await db.flush()
    return user


async def add_records(db, vehicle, tag):
    expense = Expense(
        vehicle_id=vehicle.id,
        category=ExpenseCategory.SERVICE,
        amount=Decimal("10.00"),
        date=date(2026, 1, 1),
        description=f"expense-{tag}",
    )
    record = MaintenanceRecord(
        vehicle_id=vehicle.id,
        type=MaintenanceType.REPAIR,
        title=f"job-{tag}",
        status=MaintenanceStatus.SCHEDULED,
        scheduled_date=date(2026, 2, 1),
    )
    doc = ComplianceDocument(
        vehicle_id=vehicle.id,
        document_type="casco",
        start_date=date(2026, 1, 1),
        end_date=date.today() + timedelta(days=200),
        provider=f"insurer-{tag}",
    )
    db.add_all([expense, record, doc])
    await db.flush()
    return expense, record, doc


async def login(client, username, password="driver-pass-1"):
    response = await client.post("/login", data={"username": username, "password": password})
    assert response.status_code == 302, response.text[:200]


async def get(client, path):
    return await client.get(path, follow_redirects=False)


@pytest.fixture
async def world(db_session):
    """Two drivers (A with vehicle A, B with vehicle B), one driver without vehicles."""
    drivers = {k: await make_driver(db_session, f"Driver {k}") for k in ("a", "b", "none")}
    vehicles = {
        "a": await make_vehicle(db_session, "CA1111AA", drivers["a"]),
        "b": await make_vehicle(db_session, "CB2222BB", drivers["b"]),
    }
    records = {k: await add_records(db_session, v, k) for k, v in vehicles.items()}
    users = {k: await make_driver_login(db_session, drivers[k], f"driver_{k}") for k in drivers}
    await db_session.commit()
    return {"drivers": drivers, "vehicles": vehicles, "records": records, "users": users}


# --- policy (pure) ----------------------------------------------------------------------------------


def test_policy_without_vehicles_allows_only_profile_and_account_pages():
    did = uuid4()
    scope = DriverScope(restricted=True, driver_id=did)
    for path in (
        f"/drivers/{did}",
        f"/drivers/{did}/tab/licences",
        "/profile",
        "/help",
        "/notifications/bell",
        "/logout",
    ):
        assert driver_may_open(path, "GET", scope), path
    for path in (
        "/",
        "/vehicles",
        "/expenses",
        "/maintenance",
        "/compliance",
        "/reports/tco",
        "/drivers",
        f"/drivers/{uuid4()}",
        "/settings/users",
        "/widgets/fleet-overview",
        f"/vehicles/{uuid4()}",
    ):
        assert not driver_may_open(path, "GET", scope), path
    assert not driver_may_open(f"/drivers/{did}/edit", "GET", scope)
    assert not driver_may_open(f"/drivers/{did}", "POST", scope)
    assert driver_may_open("/profile", "POST", scope)  # change own password


def test_policy_with_vehicles_adds_read_only_vehicle_pages():
    did, vid = uuid4(), uuid4()
    scope = DriverScope(restricted=True, driver_id=did, vehicle_ids=(vid,))
    for path in (
        "/vehicles",
        f"/vehicles/{vid}",
        f"/vehicles/{vid}/tab/expenses",
        "/expenses",
        f"/expenses/{uuid4()}",
        "/maintenance",
        "/maintenance/due",
        "/compliance",
        f"/documents/gallery/vehicle/{vid}",
    ):
        assert driver_may_open(path, "GET", scope), path
    for path in (
        "/vehicles/new",
        f"/vehicles/{vid}/edit",
        "/expenses/new",
        "/maintenance/new",
        "/compliance/new",
        "/expenses/vehicle-options",
        "/reports",
        "/",
        "/drivers",
        "/documents/upload",
    ):
        assert not driver_may_open(path, "GET", scope), path
    for path in ("/vehicles/new", f"/vehicles/{vid}/delete", "/expenses/new"):
        assert not driver_may_open(path, "POST", scope), path


def test_policy_lets_a_driver_manage_their_own_documents():
    did, eid, doc = uuid4(), uuid4(), uuid4()
    scope = DriverScope(restricted=True, driver_id=did)  # no vehicle needed
    for method, path in (
        ("GET", "/driver-documents/new"),
        ("POST", "/driver-documents/new"),
        ("GET", f"/driver-documents/{eid}/edit"),
        ("POST", f"/driver-documents/{eid}/edit"),
        ("GET", f"/driver-documents/{eid}/renew"),
        ("POST", f"/driver-documents/{eid}/renew"),
        ("POST", "/documents/upload"),
        ("DELETE", f"/documents/{doc}"),
        ("GET", f"/documents/gallery/driver/{did}"),
        ("GET", f"/documents/gallery/driver_document/{eid}"),
        ("GET", f"/documents/{doc}/download"),
    ):
        assert driver_may_open(path, method, scope), (method, path)
    # entries themselves can never be deleted, and nothing outside the driver's own documents opens up
    for method, path in (
        ("POST", f"/driver-documents/{eid}/delete"),
        ("POST", f"/drivers/{did}/edit"),
        ("POST", f"/drivers/{did}/vehicles"),
        ("GET", f"/documents/gallery/vehicle/{eid}"),
        ("DELETE", "/vehicles/x"),
    ):
        assert not driver_may_open(path, method, scope), (method, path)


def test_api_policy_is_deny_by_default():
    did, vid = uuid4(), uuid4()
    nothing = DriverScope(restricted=True, driver_id=did)
    mine = DriverScope(restricted=True, driver_id=did, vehicle_ids=(vid,))
    assert driver_may_call_api("/api/v1/auth/me", "GET", nothing)
    assert driver_may_call_api(f"/api/v1/drivers/{did}", "GET", nothing)
    assert not driver_may_call_api("/api/v1/vehicles", "GET", nothing)
    assert not driver_may_call_api("/api/v1/mileage", "POST", nothing)
    assert driver_may_call_api("/api/v1/vehicles", "GET", mine) and driver_may_call_api("/api/v1/mileage", "POST", mine)
    for path in (
        "/api/v1/expenses",
        "/api/v1/maintenance",
        "/api/v1/compliance-documents",
        "/api/v1/reports/tco",
        "/api/v1/users",
        "/api/v1/drivers",
        f"/api/v1/drivers/{uuid4()}",
    ):
        assert not driver_may_call_api(path, "GET", mine), path
    assert not driver_may_call_api("/api/v1/vehicles", "POST", mine)


@pytest.mark.asyncio
async def test_resolve_scope(db_session, world):
    users = world["users"]
    assert await resolve_scope(db_session, users["a"]) == DriverScope(
        True, world["drivers"]["a"].id, (world["vehicles"]["a"].id,)
    )
    assert (await resolve_scope(db_session, users["none"])).has_vehicles is False
    manager = User(
        id=uuid4(), email="m@x.com", username="m", full_name="M", hashed_password="x", role=UserRole.FLEET_MANAGER
    )
    assert (await resolve_scope(db_session, manager)).restricted is False
    # a terminated driver keeps the profile but loses vehicle access
    world["drivers"]["a"].status = DriverStatus.TERMINATED
    await db_session.flush()
    scope = await resolve_scope(db_session, users["a"])
    assert scope.driver_id == world["drivers"]["a"].id and not scope.has_vehicles


# --- web: driver without vehicles -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_driver_without_vehicles_sees_only_own_profile(client, world):
    own = f"/drivers/{world['drivers']['none'].id}"
    await login(client, "driver_none")
    for path in (
        "/",
        "/vehicles",
        "/expenses",
        "/maintenance",
        "/maintenance/due",
        "/compliance",
        "/reports",
        "/reports/tco",
        "/drivers",
        f"/drivers/{world['drivers']['a'].id}",
        "/settings/users",
        "/settings/audit",
        "/vehicles/new",
        f"/vehicles/{world['vehicles']['a'].id}",
        f"/expenses/{world['records']['a'][0].id}",
    ):
        response = await get(client, path)
        assert response.status_code == 302 and response.headers["location"] == own, path
    page = await client.get(own)
    assert page.status_code == 200 and "Driver none" in page.text
    for path in ("/profile", "/help"):
        assert (await client.get(path)).status_code == 200
    assert f'href="{own}"' in page.text and "My profile" in page.text
    for hidden in (
        'href="/vehicles"',
        'href="/expenses"',
        'href="/maintenance"',
        'href="/compliance"',
        'href="/reports"',
        'href="/drivers"',
        'href="/"',
    ):
        assert hidden not in page.text.split("<main")[0], hidden  # sidebar and header
    # HTMX fragments of closed pages answer 403, not a redirect
    assert (await client.get("/widgets/fleet-overview", headers={"HX-Request": "true"})).status_code == 403


@pytest.mark.asyncio
async def test_driver_cannot_write_anything(client, db_session, world):
    await login(client, "driver_a")
    vid = world["vehicles"]["a"].id
    for method, path, data in (
        ("post", "/vehicles/new", {"license_plate": "ZZ"}),
        ("post", f"/vehicles/{vid}/edit", {"brand": "X"}),
        ("post", f"/vehicles/{vid}/delete", {}),
        ("post", "/expenses/new", {}),
        ("post", f"/drivers/{world['drivers']['a'].id}/vehicles", {"vehicle_id": str(world["vehicles"]["b"].id)}),
        ("post", "/settings/users/new", {}),
    ):
        response = await getattr(client, method)(path, data=data, follow_redirects=False)
        assert response.status_code in (302, 303), path
    await db_session.refresh(world["vehicles"]["a"])
    assert world["vehicles"]["a"].brand == "Volvo"
    assert (await db_session.get(Driver, world["drivers"]["a"].id)) is not None
    b = await db_session.get(type(world["vehicles"]["b"]), world["vehicles"]["b"].id)
    await db_session.refresh(b)
    assert b.assigned_driver_id == world["drivers"]["b"].id


# --- web: driver with an assigned vehicle -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_driver_with_vehicle_sees_only_that_vehicle(client, world):
    va, vb = world["vehicles"]["a"], world["vehicles"]["b"]
    exp_a, rec_a, doc_a = world["records"]["a"]
    exp_b, rec_b, doc_b = world["records"]["b"]
    await login(client, "driver_a")

    listing = await client.get("/vehicles")
    assert "CA1111AA" in listing.text and "CB2222BB" not in listing.text
    assert (await client.get(f"/vehicles/{va.id}")).status_code == 200
    other = await get(client, f"/vehicles/{vb.id}")
    assert other.status_code == 302 and other.headers["location"] == "/vehicles"
    for tab in ("mileage", "maintenance", "expenses", "compliance"):
        assert (await client.get(f"/vehicles/{va.id}/tab/{tab}")).status_code == 200, tab
        assert (await client.get(f"/vehicles/{vb.id}/tab/{tab}")).status_code == 403, tab

    page = await client.get("/expenses")
    assert "expense-a" in page.text or "CA1111AA" in page.text
    assert "CB2222BB" not in page.text
    assert (await client.get(f"/expenses/{exp_a.id}")).status_code == 200
    assert (await get(client, f"/expenses/{exp_b.id}")).headers["location"] == "/expenses"

    page = await client.get("/maintenance")
    assert "job-a" in page.text and "job-b" not in page.text
    assert (await client.get(f"/maintenance/{rec_a.id}")).status_code == 200
    assert (await get(client, f"/maintenance/{rec_b.id}")).headers["location"] == "/maintenance"
    assert "CB2222BB" not in (await client.get("/maintenance/due")).text

    page = await client.get("/compliance?include_history=true")
    assert "CA1111AA" in page.text and "CB2222BB" not in page.text
    assert (await client.get(f"/compliance/{doc_a.id}")).status_code == 200
    assert (await get(client, f"/compliance/{doc_b.id}")).headers["location"] == "/compliance"

    # files of other drivers' vehicles are closed too
    for etype, eid in (
        ("vehicle", vb.id),
        ("maintenance", rec_b.id),
        ("expense", exp_b.id),
        ("compliance_document", doc_b.id),
    ):
        assert (await client.get(f"/documents/gallery/{etype}/{eid}")).status_code == 403, etype
    for etype, eid in (
        ("vehicle", va.id),
        ("maintenance", rec_a.id),
        ("expense", exp_a.id),
        ("compliance_document", doc_a.id),
    ):
        assert (await client.get(f"/documents/gallery/{etype}/{eid}")).status_code == 200, etype

    # still closed: dashboard, reports, other drivers, forms
    own = f"/drivers/{world['drivers']['a'].id}"
    for path in (
        "/",
        "/reports",
        "/drivers",
        f"/drivers/{world['drivers']['b'].id}",
        "/vehicles/new",
        "/expenses/new",
        f"/vehicles/{va.id}/edit",
    ):
        assert (await get(client, path)).headers["location"] == own, path
    sidebar = (await client.get(own)).text.split("<main")[0]
    for shown in ('href="/vehicles"', 'href="/expenses"', 'href="/maintenance"', 'href="/compliance"', f'href="{own}"'):
        assert shown in sidebar, shown
    for hidden in ('href="/reports"', 'href="/drivers"', 'href="/"'):
        assert hidden not in sidebar, hidden


@pytest.mark.asyncio
async def test_terminated_driver_loses_vehicle_access(client, db_session, world):
    world["drivers"]["a"].status = DriverStatus.TERMINATED
    await db_session.commit()
    await login(client, "driver_a")
    assert (await get(client, "/vehicles")).status_code == 302
    assert (await client.get(f"/drivers/{world['drivers']['a'].id}")).status_code == 200


@pytest.mark.asyncio
async def test_legacy_driver_login_without_profile_sees_only_account_pages(client, db_session):
    user = User(
        id=uuid4(),
        email="old@x.com",
        username="olddriver",
        full_name="Old Driver",
        role=UserRole.DRIVER,
        hashed_password=__import__("app.utils.security", fromlist=["x"]).hash_password("driver-pass-1"),
        is_active=True,
        language="en",
    )
    db_session.add(user)
    await db_session.commit()
    await login(client, "olddriver")
    for path in ("/", "/vehicles", "/drivers"):
        assert (await get(client, path)).headers["location"] == "/profile"
    assert (await client.get("/profile")).status_code == 200


@pytest.mark.asyncio
async def test_managers_and_viewers_are_not_restricted(client, db_session, world, fleet_manager_user, viewer_user):
    await db_session.commit()
    for username in ("testmanager", "testviewer"):
        await client.get("/logout")
        assert (await client.post("/login", data={"username": username, "password": "testpass123"})).status_code == 302
        page = await client.get("/vehicles")
        assert "CA1111AA" in page.text and "CB2222BB" in page.text
        assert (await client.get("/reports")).status_code == 200


@pytest.mark.asyncio
async def test_vehicle_forms_are_for_managers_only(client, db_session, viewer_user):
    await db_session.commit()
    assert (await client.post("/login", data={"username": "testviewer", "password": "testpass123"})).status_code == 302
    assert (await get(client, "/vehicles/new")).headers["location"] == "/vehicles"
    assert (await client.post("/vehicles/new", data={"license_plate": "X"}, follow_redirects=False)).status_code == 303


# --- API ---------------------------------------------------------------------------------------------------


def bearer(user):
    return {"Authorization": "Bearer " + create_access_token(user.id, user.role.value)}


@pytest.mark.asyncio
async def test_api_driver_access(client, world):
    ua, un = world["users"]["a"], world["users"]["none"]
    va, vb = world["vehicles"]["a"], world["vehicles"]["b"]
    listing = await client.get("/api/v1/vehicles", headers=bearer(ua))
    assert [v["license_plate"] for v in listing.json()["items"]] == ["CA1111AA"] and listing.json()["total"] == 1
    assert (await client.get(f"/api/v1/vehicles/{va.id}", headers=bearer(ua))).status_code == 200
    assert (await client.get(f"/api/v1/vehicles/{vb.id}", headers=bearer(ua))).status_code == 404
    for path in (
        "/api/v1/expenses",
        "/api/v1/maintenance",
        "/api/v1/compliance-documents",
        "/api/v1/reports/tco",
        "/api/v1/users",
        "/api/v1/drivers",
        f"/api/v1/drivers/{world['drivers']['b'].id}",
    ):
        assert (await client.get(path, headers=bearer(ua))).status_code == 403, path
    assert (await client.get(f"/api/v1/drivers/{world['drivers']['a'].id}", headers=bearer(ua))).status_code == 200
    assert (await client.get("/api/v1/auth/me", headers=bearer(ua))).status_code == 200
    # mileage only for own vehicles
    assert (
        await client.post("/api/v1/mileage", json={"vehicle_id": str(vb.id), "value": 5000}, headers=bearer(ua))
    ).status_code == 403
    assert (await client.get(f"/api/v1/mileage/vehicle/{vb.id}", headers=bearer(ua))).status_code == 403
    ok = await client.post("/api/v1/mileage", json={"vehicle_id": str(va.id), "value": 5000}, headers=bearer(ua))
    assert ok.status_code == 201, ok.text
    # without vehicles nothing but the own profile
    assert (await client.get("/api/v1/vehicles", headers=bearer(un))).status_code == 403
    assert (await client.get(f"/api/v1/drivers/{world['drivers']['none'].id}", headers=bearer(un))).status_code == 200
    assert (
        await client.post("/api/v1/mileage", json={"vehicle_id": str(va.id), "value": 6000}, headers=bearer(un))
    ).status_code == 403


@pytest.mark.asyncio
async def test_api_documents_of_other_vehicles_are_closed(client, world):
    ua = world["users"]["a"]
    mine = await client.get(f"/api/v1/documents/entity/vehicle/{world['vehicles']['a'].id}", headers=bearer(ua))
    other = await client.get(f"/api/v1/documents/entity/vehicle/{world['vehicles']['b'].id}", headers=bearer(ua))
    assert mine.status_code == 200 and other.status_code == 403
