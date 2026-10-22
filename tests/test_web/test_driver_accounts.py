"""Creating driver logins (profile first) and assigning vehicles to drivers."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.driver import DriverStatus
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
    # assigning it to another driver moves it
    await client.post(f"/drivers/{two.id}/vehicles", data={"vehicle_id": str(vehicle.id)})
    await db_session.refresh(vehicle)
    assert vehicle.assigned_driver_id == two.id
    # a driver cannot remove a vehicle that is not theirs
    await client.post(f"/drivers/{one.id}/vehicles/{vehicle.id}/unassign")
    await db_session.refresh(vehicle)
    assert vehicle.assigned_driver_id == two.id
    await client.post(f"/drivers/{two.id}/vehicles/{vehicle.id}/unassign")
    await db_session.refresh(vehicle)
    assert vehicle.assigned_driver_id is None
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
