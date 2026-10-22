"""Creating driver logins (profile first) and assigning vehicles to drivers."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.driver import Driver, DriverStatus
from app.models.user import User, UserRole
from tests.conftest import auth_header
from tests.test_web.test_driver_access import bearer, login, make_driver, make_driver_login, make_vehicle  # noqa: F401


async def admin_login(client):
    assert (await client.post("/login", data={"username": "testadmin", "password": "testpass123"})).status_code == 302
    await client.get("/set-language/en")


def form(driver_id="", role="driver", username="newdriver", **extra):
    return {
        "username": username,
        "full_name": "New Driver",
        "email": f"{username}@example.com",
        "role": role,
        "language": "en",
        "password": "secret-123",
        "password_confirm": "secret-123",
        "driver_id": str(driver_id),
        **extra,
    }


async def user_named(db, username):
    return await db.scalar(select(User).where(User.username == username))


# --- web: creating the login ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_form_offers_only_free_active_profiles(client, db_session, admin_user):
    free = await make_driver(db_session, "Free Driver")
    taken = await make_driver(db_session, "Taken Driver")
    await make_driver_login(db_session, taken, "taken")
    await make_driver(db_session, "Gone Driver", DriverStatus.TERMINATED)
    await db_session.commit()
    await admin_login(client)
    page = await client.get("/settings/users/new")
    assert "Free Driver" in page.text and f'value="{free.id}"' in page.text
    assert "Taken Driver" not in page.text and "Gone Driver" not in page.text


@pytest.mark.asyncio
async def test_form_without_free_profiles_points_to_drivers(client, db_session, admin_user):
    await db_session.commit()
    await admin_login(client)
    page = await client.get("/settings/users/new")
    assert "no driver profile without a login" in page.text and 'href="/drivers/new"' in page.text


@pytest.mark.asyncio
async def test_driver_login_requires_a_profile(client, db_session, admin_user):
    await db_session.commit()
    await admin_login(client)
    response = await client.post("/settings/users/new", data=form(driver_id=""))
    assert response.status_code == 422 and "must be linked to a driver profile" in response.text
    assert await user_named(db_session, "newdriver") is None
    unknown = await client.post("/settings/users/new", data=form(driver_id=uuid4()))
    assert unknown.status_code == 422 and "was not found" in unknown.text
    assert await user_named(db_session, "newdriver") is None


@pytest.mark.asyncio
async def test_driver_login_is_linked_to_the_profile_and_can_sign_in(client, db_session, admin_user):
    driver = await make_driver(db_session, "Peter Driver")
    await db_session.commit()
    await admin_login(client)
    created = await client.post("/settings/users/new", data=form(driver.id), follow_redirects=False)
    assert created.status_code == 303
    user = await user_named(db_session, "newdriver")
    await db_session.refresh(driver)
    assert user.role == UserRole.DRIVER and driver.user_id == user.id
    listing = await client.get("/settings/users")
    assert "Peter Driver" in listing.text  # the profile is shown under the role
    assert f"/drivers/{driver.id}" in (await client.get(f"/drivers/{driver.id}")).text
    assert "newdriver" in (await client.get(f"/drivers/{driver.id}")).text  # login account on the profile
    await client.get("/logout")
    await login(client, "newdriver", "secret-123")
    home = await client.get("/", follow_redirects=False)
    assert home.headers["location"] == f"/drivers/{driver.id}"


@pytest.mark.asyncio
async def test_profile_with_a_login_or_terminated_cannot_be_taken(client, db_session, admin_user):
    linked = await make_driver(db_session, "Linked")
    await make_driver_login(db_session, linked, "linked")
    gone = await make_driver(db_session, "Gone", DriverStatus.TERMINATED)
    await db_session.commit()
    await admin_login(client)
    taken = await client.post("/settings/users/new", data=form(linked.id))
    assert taken.status_code == 422 and "already has a login" in taken.text
    ended = await client.post("/settings/users/new", data=form(gone.id))
    assert ended.status_code == 422 and "terminated" in ended.text
    assert await user_named(db_session, "newdriver") is None


@pytest.mark.asyncio
async def test_other_roles_do_not_link_a_profile(client, db_session, admin_user):
    driver = await make_driver(db_session, "Stays Free")
    await db_session.commit()
    await admin_login(client)
    created = await client.post(
        "/settings/users/new", data=form(driver.id, role="viewer", username="watcher"), follow_redirects=False
    )
    assert created.status_code == 303  # the submitted profile is ignored for non-driver roles
    await db_session.refresh(driver)
    assert driver.user_id is None


@pytest.mark.asyncio
async def test_changing_roles_updates_the_link(client, db_session, admin_user, viewer_user):
    first, second = await make_driver(db_session, "First"), await make_driver(db_session, "Second")
    await db_session.commit()
    await admin_login(client)
    base = {"full_name": "V", "email": viewer_user.email, "language": "en", "is_active": "on"}
    # viewer -> driver needs a profile
    no_profile = await client.post(f"/settings/users/{viewer_user.id}/edit", data={**base, "role": "driver"})
    assert no_profile.status_code == 422 and "must be linked" in no_profile.text
    assert (
        await client.post(
            f"/settings/users/{viewer_user.id}/edit",
            data={**base, "role": "driver", "driver_id": str(first.id)},
            follow_redirects=False,
        )
    ).status_code == 303
    await db_session.refresh(first)
    assert first.user_id == viewer_user.id
    # the form shows the current profile and the free ones
    edit = await client.get(f"/settings/users/{viewer_user.id}/edit")
    assert f'value="{first.id}" selected' in edit.text and "Second" in edit.text
    # moving to another profile frees the first
    await client.post(
        f"/settings/users/{viewer_user.id}/edit", data={**base, "role": "driver", "driver_id": str(second.id)}
    )
    await db_session.refresh(first)
    await db_session.refresh(second)
    assert first.user_id is None and second.user_id == viewer_user.id
    # driver -> viewer removes the link
    await client.post(f"/settings/users/{viewer_user.id}/edit", data={**base, "role": "viewer"})
    await db_session.refresh(second)
    assert second.user_id is None


# --- API -----------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_user_rules(client, db_session, admin_token, admin_user):
    driver = await make_driver(db_session, "Api Driver")
    await db_session.commit()
    headers = auth_header(admin_token)
    payload = {
        "email": "api@example.com",
        "username": "apidriver",
        "full_name": "Api",
        "password": "secret-123",
        "role": "driver",
    }
    missing = await client.post("/api/v1/users", json=payload, headers=headers)
    assert missing.status_code == 400
    wrong_role = await client.post(
        "/api/v1/users", json={**payload, "role": "viewer", "driver_id": str(driver.id)}, headers=headers
    )
    assert wrong_role.status_code == 400
    ok = await client.post("/api/v1/users", json={**payload, "driver_id": str(driver.id)}, headers=headers)
    assert ok.status_code == 201, ok.text
    await db_session.refresh(driver)
    assert driver.user_id is not None
    again = await client.post(
        "/api/v1/users",
        json={**payload, "username": "second", "email": "s@example.com", "driver_id": str(driver.id)},
        headers=headers,
    )
    assert again.status_code == 400
    # deactivating a driver login keeps the link
    uid = ok.json()["id"]
    assert (await client.patch(f"/api/v1/users/{uid}", json={"is_active": False}, headers=headers)).status_code == 200
    await db_session.refresh(driver)
    assert driver.user_id is not None
    # promoting to another role unlinks
    assert (await client.patch(f"/api/v1/users/{uid}", json={"role": "viewer"}, headers=headers)).status_code == 200
    await db_session.refresh(driver)
    assert driver.user_id is None


@pytest.mark.asyncio
async def test_api_driver_profile_with_login_id_is_checked(client, db_session, admin_token, viewer_user):
    headers = auth_header(admin_token)
    wrong = await client.post(
        "/api/v1/drivers", json={"full_name": "X", "user_id": str(viewer_user.id)}, headers=headers
    )
    assert wrong.status_code == 400
    # a driver-role login that already has a profile cannot be linked to a second one
    driver = await make_driver(db_session, "Has Login")
    login_user = await make_driver_login(db_session, driver, "hasone")
    twice = await client.post(
        "/api/v1/drivers", json={"full_name": "Y", "user_id": str(login_user.id)}, headers=headers
    )
    assert twice.status_code == 400


# --- assigning vehicles -------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manager_assigns_and_removes_vehicles(client, db_session, admin_user):
    one, two = await make_driver(db_session, "One"), await make_driver(db_session, "Two")
    vehicle = await make_vehicle(db_session, "CA7777VV")
    await db_session.commit()
    await admin_login(client)
    page = await client.get(f"/drivers/{one.id}")
    assert f'hx-get="/drivers/{one.id}/vehicle-options"' in page.text and "Assign vehicle" in page.text
    options = await client.get(f"/drivers/{one.id}/vehicle-options?q=7777")
    assert "CA7777VV" in options.text and str(vehicle.id) in options.text
    assert (
        await client.post(f"/drivers/{one.id}/vehicles", data={"vehicle_id": str(vehicle.id)}, follow_redirects=False)
    ).status_code == 303
    await db_session.refresh(vehicle)
    assert vehicle.assigned_driver_id == one.id
    assert "CA7777VV" in (await client.get(f"/drivers/{one.id}")).text
    # a vehicle that has a driver is not taken over by another driver; it must be unassigned first
    await client.post(f"/drivers/{two.id}/vehicles", data={"vehicle_id": str(vehicle.id)})
    await db_session.refresh(vehicle)
    assert vehicle.assigned_driver_id == one.id
    # a driver cannot remove a vehicle that is not theirs
    await client.post(f"/drivers/{two.id}/vehicles/{vehicle.id}/unassign")
    await db_session.refresh(vehicle)
    assert vehicle.assigned_driver_id == one.id
    await client.post(f"/drivers/{one.id}/vehicles/{vehicle.id}/unassign")
    await db_session.refresh(vehicle)
    assert vehicle.assigned_driver_id is None
    # once free it can go to the other driver
    await client.post(f"/drivers/{two.id}/vehicles", data={"vehicle_id": str(vehicle.id)})
    await db_session.refresh(vehicle)
    assert vehicle.assigned_driver_id == two.id
    # invalid input changes nothing
    assert (
        await client.post(f"/drivers/{one.id}/vehicles", data={"vehicle_id": "nope"}, follow_redirects=False)
    ).status_code == 303


@pytest.mark.asyncio
async def test_terminated_driver_cannot_get_a_vehicle(client, db_session, admin_user):
    gone = await make_driver(db_session, "Gone", DriverStatus.TERMINATED)
    vehicle = await make_vehicle(db_session, "CA8888GG")
    await db_session.commit()
    await admin_login(client)
    await client.post(f"/drivers/{gone.id}/vehicles", data={"vehicle_id": str(vehicle.id)})
    await db_session.refresh(vehicle)
    assert vehicle.assigned_driver_id is None
    assert "Assign vehicle" not in (await client.get(f"/drivers/{gone.id}")).text


@pytest.mark.asyncio
async def test_assigning_a_vehicle_gives_the_driver_access_and_it_is_audited(client, db_session, admin_user):
    from app.models.audit_log import AuditAction, AuditLog

    driver = await make_driver(db_session, "Mia")
    await make_driver_login(db_session, driver, "mia")
    vehicle = await make_vehicle(db_session, "CA9999MM")
    await db_session.commit()
    await admin_login(client)
    await client.post(f"/drivers/{driver.id}/vehicles", data={"vehicle_id": str(vehicle.id)})
    entries = (
        await db_session.scalars(
            select(AuditLog).where(
                AuditLog.entity_id == vehicle.id,
                AuditLog.entity_type == "vehicle",
                AuditLog.action == AuditAction.UPDATE,
            )
        )
    ).all()
    assignment = [e for e in entries if "assigned_driver_id" in (e.changes or {})]
    assert len(assignment) == 1 and assignment[0].user_id == admin_user.id
    assert assignment[0].changes["assigned_driver_id"] == {"old": None, "new": str(driver.id)}
    await client.get("/logout")
    await login(client, "mia")
    assert "CA9999MM" in (await client.get("/vehicles")).text


# --- a driver login takes its name and email from the driver profile ---------------------------------------


async def profile(db, name="Georgi Petrov", email="georgi@fleet.test"):
    driver = Driver(id=uuid4(), full_name=name, email=email, status=DriverStatus.ACTIVE)
    db.add(driver)
    await db.flush()
    return driver


@pytest.mark.asyncio
async def test_form_carries_profile_data_for_prefill(client, db_session, admin_user):
    with_email = await profile(db_session, "With Email", "with@fleet.test")
    without = await profile(db_session, "No Email", None)
    await db_session.commit()
    await admin_login(client)
    page = await client.get("/settings/users/new")
    assert "With Email" in page.text and "with@fleet.test" in page.text  # profile data for the form
    assert str(with_email.id) in page.text and str(without.id) in page.text
    assert ":readonly=" in page.text and 'name="username"' in page.text


@pytest.mark.asyncio
async def test_driver_login_takes_name_and_email_from_the_profile(client, db_session, admin_user):
    driver = await profile(db_session)
    await db_session.commit()
    await admin_login(client)
    created = await client.post(
        "/settings/users/new",
        data=form(driver.id, full_name="Typed Name", email="typed@other.test"),
        follow_redirects=False,
    )
    assert created.status_code == 303
    user = await user_named(db_session, "newdriver")
    await db_session.refresh(driver)
    assert (user.full_name, user.email) == ("Georgi Petrov", "georgi@fleet.test")  # what was typed is ignored
    assert driver.email == "georgi@fleet.test"  # the profile is unchanged


@pytest.mark.asyncio
async def test_profile_without_email_gets_the_entered_one(client, db_session, admin_user):
    driver = await profile(db_session, "No Mail", None)
    await db_session.commit()
    await admin_login(client)
    # an email is required when the profile has none
    missing = await client.post("/settings/users/new", data=form(driver.id, email=""))
    assert missing.status_code == 422 and "valid email" in missing.text
    assert await user_named(db_session, "newdriver") is None
    await db_session.refresh(driver)
    assert driver.email is None
    created = await client.post(
        "/settings/users/new", data=form(driver.id, email="entered@fleet.test"), follow_redirects=False
    )
    assert created.status_code == 303
    user = await user_named(db_session, "newdriver")
    await db_session.refresh(driver)
    assert user.email == "entered@fleet.test" and user.full_name == "No Mail"
    assert driver.email == "entered@fleet.test"  # the profile is updated too


@pytest.mark.asyncio
async def test_failed_creation_changes_nothing_on_the_profile(client, db_session, admin_user, viewer_user):
    driver = await profile(db_session, "Clash", None)
    await db_session.commit()
    await admin_login(client)
    taken_email = await client.post("/settings/users/new", data=form(driver.id, email=viewer_user.email))
    assert taken_email.status_code == 422 and "already registered" in taken_email.text
    taken_name = await client.post(
        "/settings/users/new", data=form(driver.id, username="testviewer", email="x@fleet.test")
    )
    assert taken_name.status_code == 422 and "already taken" in taken_name.text
    await db_session.refresh(driver)
    assert driver.email is None and driver.user_id is None


@pytest.mark.asyncio
async def test_editing_a_driver_login_keeps_profile_values_and_username_can_change(client, db_session, admin_user):
    driver = await profile(db_session, "Edit Me", None)
    other = await make_driver_login(db_session, await profile(db_session, "Someone", "someone@fleet.test"), "someone")
    await db_session.commit()
    await admin_login(client)
    await client.post("/settings/users/new", data=form(driver.id, email="first@fleet.test"))
    user = await user_named(db_session, "newdriver")
    page = await client.get(f"/settings/users/{user.id}/edit")
    assert 'name="username"' in page.text and "disabled" not in page.text.split('name="username"')[0][-200:]
    base = {"language": "en", "role": "driver", "driver_id": str(driver.id), "is_active": "on"}
    # the typed name / email are ignored: the profile (which now has the email) is the source
    changed = await client.post(
        f"/settings/users/{user.id}/edit",
        data={**base, "username": "renamed_driver", "full_name": "Hacked", "email": "hacked@fleet.test"},
        follow_redirects=False,
    )
    assert changed.status_code == 303
    await db_session.refresh(user)
    assert (user.username, user.full_name, user.email) == ("renamed_driver", "Edit Me", "first@fleet.test")
    # usernames stay unique
    clash = await client.post(f"/settings/users/{user.id}/edit", data={**base, "username": other.username})
    assert clash.status_code == 422 and "already taken" in clash.text
    short = await client.post(f"/settings/users/{user.id}/edit", data={**base, "username": "ab"})
    assert short.status_code == 422
    await db_session.refresh(user)
    assert user.username == "renamed_driver"


@pytest.mark.asyncio
async def test_other_roles_keep_free_name_and_email_and_driver_switch_takes_profile(
    client, db_session, admin_user, viewer_user
):
    driver = await profile(db_session, "Profile Name", "profile@fleet.test")
    await db_session.commit()
    await admin_login(client)
    free = await client.post(
        "/settings/users/new",
        data=form("", role="viewer", username="free", full_name="Free Name", email="free@fleet.test"),
    )
    assert free.status_code in (302, 303)
    free_user = await user_named(db_session, "free")
    assert (free_user.full_name, free_user.email) == ("Free Name", "free@fleet.test")
    base = {"language": "en", "is_active": "on", "full_name": "V", "email": viewer_user.email}
    await client.post(
        f"/settings/users/{viewer_user.id}/edit", data={**base, "role": "driver", "driver_id": str(driver.id)}
    )
    await db_session.refresh(viewer_user)
    assert (viewer_user.full_name, viewer_user.email) == ("Profile Name", "profile@fleet.test")


@pytest.mark.asyncio
async def test_api_driver_login_takes_profile_values(client, db_session, admin_token, admin_user):
    driver = await profile(db_session, "Api Profile", "apiprofile@fleet.test")
    await db_session.commit()
    created = await client.post(
        "/api/v1/users",
        json={
            "email": "typed@x.test",
            "username": "apiuser",
            "full_name": "Typed",
            "password": "secret-123",
            "role": "driver",
            "driver_id": str(driver.id),
        },
        headers=auth_header(admin_token),
    )
    assert created.status_code == 201, created.text
    assert (created.json()["full_name"], created.json()["email"]) == ("Api Profile", "apiprofile@fleet.test")


# --- only trucks and cars can be assigned to a driver --------------------------------------------------------


async def vehicle_of(db, kind, plate=None):
    from app.schemas.vehicle import VehicleCreate
    from app.services.vehicle_service import VehicleService

    return await VehicleService(db).create(
        VehicleCreate(
            vehicle_type=kind,
            license_plate=None if kind == "forklift" else plate or "CA" + uuid4().hex[:6].upper(),
            brand="Brand",
            model="Model",
            department="Test department",
        )
    )


KINDS = ("tractor_unit", "van", "passenger_car", "container_semi_trailer", "curtain_side_semi_trailer", "forklift")
ASSIGNABLE = {"tractor_unit", "van", "passenger_car"}


def test_the_rule_lives_in_one_place():
    from app.vehicle_rules import DRIVER_ASSIGNABLE_TYPES, can_assign_to_driver

    assert {t.value for t in DRIVER_ASSIGNABLE_TYPES} == ASSIGNABLE
    assert {k for k in KINDS if can_assign_to_driver(k)} == ASSIGNABLE


@pytest.mark.asyncio
async def test_the_assign_picker_only_offers_trucks_and_cars(client, db_session, admin_user):
    driver = await make_driver(db_session, "Picker Driver")
    plates = {}
    for kind in KINDS:
        v = await vehicle_of(
            db_session, kind, f"PK{len(plates)}0000{kind[:2].upper()}"[:10] if kind != "forklift" else None
        )
        plates[kind] = v.license_plate
    await db_session.commit()
    await admin_login(client)
    options = (await client.get(f"/drivers/{driver.id}/vehicle-options")).text
    for kind in KINDS:
        assert (plates[kind] in options) == (kind in ASSIGNABLE), kind
    assert "trailers or forklifts" in (await client.get(f"/drivers/{driver.id}")).text


@pytest.mark.asyncio
async def test_trailers_and_forklifts_cannot_be_assigned_even_by_a_crafted_request(client, db_session, admin_user):
    vehicles = {kind: await vehicle_of(db_session, kind) for kind in KINDS}
    drivers = {kind: await make_driver(db_session, f"Crafted {kind}") for kind in KINDS}  # one driver each
    await db_session.commit()
    await admin_login(client)
    for kind, v in vehicles.items():
        await client.post(f"/drivers/{drivers[kind].id}/vehicles", data={"vehicle_id": str(v.id)})
        await db_session.refresh(v)
        assert (v.assigned_driver_id == drivers[kind].id) == (kind in ASSIGNABLE), kind


@pytest.mark.asyncio
async def test_vehicle_api_applies_the_same_rule(client, db_session, manager_token, fleet_manager_user):
    driver = await make_driver(db_session, "Api Driver")
    await db_session.commit()
    headers = auth_header(manager_token)
    base = {"brand": "B", "model": "M", "department": "Test department"}
    from app.config import settings  # noqa: F401  (departments are configured by the test fixtures)

    trailer = await client.post(
        "/api/v1/vehicles",
        json={
            **base,
            "vehicle_type": "container_semi_trailer",
            "license_plate": "TR1111AA",
            "assigned_driver_id": str(driver.id),
        },
        headers=headers,
    )
    assert trailer.status_code == 400 and "trucks and cars" in str(trailer.json())
    truck = await client.post(
        "/api/v1/vehicles",
        json={
            **base,
            "vehicle_type": "tractor_unit",
            "license_plate": "TK1111AA",
            "assigned_driver_id": str(driver.id),
        },
        headers=headers,
    )
    assert truck.status_code == 201 and truck.json()["assigned_driver_id"] == str(driver.id)
    # a truck that becomes a trailer is no longer assigned; assigning a trailer through an update is refused
    to_trailer = await client.patch(
        f"/api/v1/vehicles/{truck.json()['id']}", json={"vehicle_type": "container_semi_trailer"}, headers=headers
    )
    assert to_trailer.status_code == 200 and to_trailer.json()["assigned_driver_id"] is None
    refused = await client.patch(
        f"/api/v1/vehicles/{truck.json()['id']}", json={"assigned_driver_id": str(driver.id)}, headers=headers
    )
    assert refused.status_code == 400


# --- one driver, one vehicle ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vehicles_that_have_a_driver_are_not_offered(client, db_session, admin_user):
    taken_by, other = await make_driver(db_session, "Has A Vehicle"), await make_driver(db_session, "Looking")
    await make_vehicle(db_session, "CA1000TT", taken_by)
    await make_vehicle(db_session, "CA2000FF")
    await db_session.commit()
    await admin_login(client)
    options = (await client.get(f"/drivers/{other.id}/vehicle-options")).text
    assert "CA2000FF" in options and "CA1000TT" not in options
    assert "CA1000TT" not in (await client.get(f"/drivers/{other.id}/vehicle-options?q=1000")).text


@pytest.mark.asyncio
async def test_a_driver_with_a_vehicle_cannot_get_a_second_one(client, db_session, admin_user):
    driver = await make_driver(db_session, "One Only")
    mine = await make_vehicle(db_session, "CA3000MM", driver)
    second = await make_vehicle(db_session, "CA4000SS")
    await db_session.commit()
    await admin_login(client)
    page = await client.get(f"/drivers/{driver.id}")
    assert "CA3000MM" in page.text and "only one vehicle" in page.text
    assert (
        f'hx-get="/drivers/{driver.id}/vehicle-options"' not in page.text and "Assign vehicle" not in page.text
    )  # no form
    # a crafted request is refused as well
    await client.post(f"/drivers/{driver.id}/vehicles", data={"vehicle_id": str(second.id)})
    await db_session.refresh(second)
    await db_session.refresh(mine)
    assert second.assigned_driver_id is None and mine.assigned_driver_id == driver.id
    # after removing the current one the form is back and a new vehicle can be assigned
    await client.post(f"/drivers/{driver.id}/vehicles/{mine.id}/unassign")
    assert "Assign vehicle" in (await client.get(f"/drivers/{driver.id}")).text
    await client.post(f"/drivers/{driver.id}/vehicles", data={"vehicle_id": str(second.id)})
    await db_session.refresh(second)
    assert second.assigned_driver_id == driver.id


@pytest.mark.asyncio
async def test_the_database_itself_refuses_two_vehicles_for_one_driver(db_session):
    from sqlalchemy.exc import IntegrityError

    driver = await make_driver(db_session, "Race")
    first = await make_vehicle(db_session, "CA5000RR", driver)
    second = await make_vehicle(db_session, "CA6000RR")
    second.assigned_driver_id = driver.id
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()
    assert first is not None


@pytest.mark.asyncio
async def test_vehicle_api_keeps_one_driver_one_vehicle(client, db_session, manager_token, fleet_manager_user):
    driver, other = await make_driver(db_session, "Api One"), await make_driver(db_session, "Api Two")
    await db_session.commit()
    headers = auth_header(manager_token)
    base = {"brand": "B", "model": "M", "department": "Test department", "vehicle_type": "tractor_unit"}
    first = await client.post(
        "/api/v1/vehicles",
        json={**base, "license_plate": "AP1111AA", "assigned_driver_id": str(driver.id)},
        headers=headers,
    )
    assert first.status_code == 201
    # the same driver on a second vehicle is refused
    dup = await client.post(
        "/api/v1/vehicles",
        json={**base, "license_plate": "AP2222AA", "assigned_driver_id": str(driver.id)},
        headers=headers,
    )
    assert dup.status_code == 400 and "only one" in str(dup.json())
    spare = await client.post("/api/v1/vehicles", json={**base, "license_plate": "AP3333AA"}, headers=headers)
    assert (
        await client.patch(
            f"/api/v1/vehicles/{spare.json()['id']}", json={"assigned_driver_id": str(driver.id)}, headers=headers
        )
    ).status_code == 400
    # a vehicle that has a driver is not handed to someone else, but can be released first
    taken = await client.patch(
        f"/api/v1/vehicles/{first.json()['id']}", json={"assigned_driver_id": str(other.id)}, headers=headers
    )
    assert taken.status_code == 400 and "already has a driver" in str(taken.json())
    released = await client.patch(
        f"/api/v1/vehicles/{first.json()['id']}", json={"assigned_driver_id": None}, headers=headers
    )
    assert released.status_code == 200 and released.json()["assigned_driver_id"] is None
    given = await client.patch(
        f"/api/v1/vehicles/{first.json()['id']}", json={"assigned_driver_id": str(other.id)}, headers=headers
    )
    assert given.status_code == 200 and given.json()["assigned_driver_id"] == str(other.id)
