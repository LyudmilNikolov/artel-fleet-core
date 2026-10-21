"""Web UI for drivers: simplified form, role protection, tabs, document pages, list badges, dashboard, locales."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models.driver import Driver
from app.models.driver_document import DriverDocument
from app.models.user import UserRole
from app.schemas.driver_document import DriverDocumentCreate
from app.services.driver_document_service import DriverDocumentService
from tests.test_api.test_driver_documents import make_driver, make_user

TODAY = datetime.now(UTC).date()
REMOVED = ("employee_id", "license_number", "license_category", "license_expiry", "medical_expiry")


async def login(client, username):
    response = await client.post("/login", data={"username": username, "password": "testpass123"})
    assert response.status_code == 302


async def add_entry(db, driver, document_type="id_card", number="123456789", end=None, **kw):
    return await DriverDocumentService(db).create(
        DriverDocumentCreate(
            driver_id=driver.id, document_type=document_type, document_number=number, end_date=end, **kw
        ),
        None,
    )


async def driver_count(db):
    return await db.scalar(select(func.count()).select_from(Driver))


@pytest.mark.asyncio
async def test_form_has_no_removed_fields(client, db_session, fleet_manager_user):
    await login(client, "testmanager")
    page = await client.get("/drivers/new")
    assert page.status_code == 200
    for name in REMOVED:
        assert f'name="{name}"' not in page.text
    for name in ("full_name", "phone", "email", "hire_date", "department", "notes"):
        assert f'name="{name}"' in page.text
    driver = await make_driver(db_session)
    edit = await client.get(f"/drivers/{driver.id}/edit")
    assert edit.status_code == 200 and 'name="status"' in edit.text
    for name in REMOVED:
        assert f'name="{name}"' not in edit.text


@pytest.mark.asyncio
async def test_create_edit_and_delete_as_manager(client, db_session, fleet_manager_user):
    await login(client, "testmanager")
    created = await client.post(
        "/drivers/new",
        data={"full_name": "Георги Георгиев", "phone": "+359888", "hire_date": "2020-05-01", "employee_id": "ignored"},
    )
    assert created.status_code == 302
    driver_id = created.headers["location"].rsplit("/", 1)[1]
    row = await db_session.scalar(select(Driver).where(Driver.full_name == "Георги Георгиев"))
    assert str(row.id) == driver_id and str(row.hire_date) == "2020-05-01"

    edited = await client.post(
        f"/drivers/{driver_id}/edit", data={"full_name": "Георги Г.", "phone": "", "status": "on_leave"}
    )
    assert edited.status_code == 302
    await db_session.refresh(row)
    assert row.full_name == "Георги Г." and row.phone is None and row.status.value == "on_leave"

    invalid = await client.post("/drivers/new", data={"full_name": ""})
    assert invalid.status_code == 422

    deleted = await client.post(f"/drivers/{driver_id}/delete")
    assert deleted.status_code == 303 and deleted.headers["location"] == "/drivers"
    assert await db_session.scalar(select(Driver).where(Driver.id == row.id)) is None


@pytest.mark.asyncio
async def test_non_managers_cannot_create_edit_or_delete(client, db_session, viewer_user):
    driver = await make_driver(db_session)
    await login(client, "testviewer")
    before = await driver_count(db_session)
    for method, url, data in [
        ("get", "/drivers/new", None),
        ("post", "/drivers/new", {"full_name": "X"}),
        ("get", f"/drivers/{driver.id}/edit", None),
        ("post", f"/drivers/{driver.id}/edit", {"full_name": "Hacked"}),
        ("post", f"/drivers/{driver.id}/delete", None),
        ("get", f"/driver-documents/new?driver_id={driver.id}", None),
        ("post", "/driver-documents/new", {"driver_id": str(driver.id), "document_type": "id_card"}),
    ]:
        response = await getattr(client, method)(url, **({"data": data} if data else {}))
        assert response.status_code in (302, 303) and response.headers["location"] == "/", (method, url)
    assert await driver_count(db_session) == before
    await db_session.refresh(driver)
    assert driver.full_name == "Иван Петров"
    assert await db_session.scalar(select(func.count()).select_from(DriverDocument)) == 0


@pytest.mark.asyncio
async def test_detail_tabs_per_role(client, db_session, fleet_manager_user, viewer_user):
    me_user, _ = await make_user(db_session, UserRole.DRIVER)
    mine = await make_driver(db_session, "Mine", user_id=me_user.id)
    other = await make_driver(db_session, "Other")
    await add_entry(db_session, other, "driving_licence", end=TODAY - timedelta(days=1), categories="B")

    await login(client, "testmanager")
    page = await client.get(f"/drivers/{other.id}")
    assert page.status_code == 200
    assert f'hx-get="/drivers/{other.id}/tab/licences"' in page.text
    assert f'hx-get="/documents/gallery/driver/{other.id}"' in page.text
    assert "General data" in page.text and "Uploaded documents" in page.text and "Licences &amp; Documents" in page.text
    assert "1 expired" in page.text  # summary on the General data tab
    assert 'href="/drivers/%s/edit"' % other.id in page.text and "/delete" in page.text

    await client.get("/logout")
    await login(client, "testviewer")
    page = await client.get(f"/drivers/{other.id}")
    assert "Licences &amp; Documents" in page.text and "Uploaded documents" not in page.text
    assert f"/drivers/{other.id}/edit" not in page.text

    await client.get("/logout")
    await login(client, me_user.username)
    assert "Licences &amp; Documents" in (await client.get(f"/drivers/{mine.id}")).text
    foreign = await client.get(f"/drivers/{other.id}")
    assert "Licences &amp; Documents" not in foreign.text and "Uploaded documents" not in foreign.text
    assert "1 expired" not in foreign.text


@pytest.mark.asyncio
async def test_licences_tab_partial_and_number_masking(client, db_session, fleet_manager_user, viewer_user):
    me_user, _ = await make_user(db_session, UserRole.DRIVER)
    driver = await make_driver(db_session)
    mine = await make_driver(db_session, "Mine", user_id=me_user.id)
    forever = await add_entry(db_session, driver, "id_card", number="900011112222", end=None)
    await add_entry(db_session, driver, "tachograph_card", number="BG1234567890123", end=TODAY + timedelta(days=10))
    await add_entry(db_session, mine, "id_card", number="777788889999", end=TODAY + timedelta(days=400))

    await login(client, "testmanager")
    tab = await client.get(f"/drivers/{driver.id}/tab/licences")
    assert tab.status_code == 200
    for label in ("ID card", "Driving licence", "Driver card (digital tachograph)", "Psychological test"):
        assert label in tab.text
    assert "900011112222" in tab.text and "No expiry" in tab.text and "Expiring soon" in tab.text
    assert "Missing" in tab.text  # types without an entry
    assert f"/driver-documents/new?driver_id={driver.id}&document_type=driving_licence" in tab.text
    assert f"/driver-documents/{forever.id}/renew" in tab.text

    await client.get("/logout")
    await login(client, "testviewer")
    masked = await client.get(f"/drivers/{driver.id}/tab/licences")
    assert masked.status_code == 200 and "900011112222" not in masked.text and "•••• 2222" in masked.text
    assert "/renew" not in masked.text and "/driver-documents/new" not in masked.text

    await client.get("/logout")
    await login(client, me_user.username)
    assert "777788889999" in (await client.get(f"/drivers/{mine.id}/tab/licences")).text
    assert (await client.get(f"/drivers/{driver.id}/tab/licences")).status_code == 403
    assert (await client.get(f"/drivers/{uuid4()}/tab/licences")).status_code == 404


@pytest.mark.asyncio
async def test_document_pages_create_edit_renew_delete(client, db_session, fleet_manager_user):
    driver = await make_driver(db_session)
    await login(client, "testmanager")

    form = await client.get("/driver-documents/new", params={"driver_id": str(driver.id), "document_type": "psycho_test"})
    assert form.status_code == 200 and 'selectedType: "psycho_test"' in form.text
    for code in ("id_card", "driving_licence", "tachograph_card", "psycho_test", "professional_competence"):
        assert f'"code": "{code}"' in form.text  # options come from the central definition
    assert (await client.get("/driver-documents/new")).status_code == 302

    bad = await client.post(
        "/driver-documents/new", data={"driver_id": str(driver.id), "document_type": "driving_licence", "categories": "B"}
    )
    assert bad.status_code == 422 and "The number is required for this document type" in bad.text
    wrong = await client.post(
        "/driver-documents/new",
        data={"driver_id": str(driver.id), "document_type": "id_card", "document_number": "1", "categories": "B"},
    )
    assert wrong.status_code == 422 and "Categories apply only to a driving licence" in wrong.text
    reversed_dates = await client.post(
        "/driver-documents/new",
        data={
            "driver_id": str(driver.id),
            "document_type": "id_card",
            "document_number": "1",
            "start_date": str(TODAY),
            "end_date": str(TODAY - timedelta(days=1)),
        },
    )
    assert reversed_dates.status_code == 422 and "The end date must be on or after the start date" in reversed_dates.text
    assert await db_session.scalar(select(func.count()).select_from(DriverDocument)) == 0

    created = await client.post(
        "/driver-documents/new",
        data={
            "driver_id": str(driver.id),
            "document_type": "driving_licence",
            "document_number": "L-100",
            "categories": "b, ce",
            "start_date": str(TODAY - timedelta(days=3000)),
            "end_date": str(TODAY + timedelta(days=20)),
            "notes": "",
        },
    )
    assert created.status_code == 302
    detail_url = created.headers["location"]
    detail = await client.get(detail_url)
    assert detail.status_code == 200 and "L-100" in detail.text and "B, CE" in detail.text
    assert "Expiring soon" in detail.text and "/documents/gallery/driver_document/" in detail.text

    entry_id = detail_url.rsplit("/", 1)[1]
    edit_form = await client.get(f"{detail_url}/edit")
    assert edit_form.status_code == 200 and 'value="L-100"' in edit_form.text
    # clearing the end date makes the document "no expiry"
    edited = await client.post(
        f"{detail_url}/edit", data={"document_number": "L-100", "categories": "B, CE", "end_date": "", "start_date": ""}
    )
    assert edited.status_code == 302
    assert "No expiry" in (await client.get(detail_url)).text

    renew_form = await client.get(f"{detail_url}/renew")
    assert renew_form.status_code == 200 and 'value="L-100"' in renew_form.text and "stays in the history" in renew_form.text
    renewed = await client.post(
        f"{detail_url}/renew",
        data={"document_number": "L-200", "categories": "B, C, CE", "start_date": str(TODAY), "end_date": str(TODAY + timedelta(days=3650))},
    )
    assert renewed.status_code == 302 and renewed.headers["location"] != detail_url
    old_page = await client.get(detail_url)
    assert "Replaced" in old_page.text and "Renewed by" in old_page.text
    tab = await client.get(f"/drivers/{driver.id}/tab/licences")
    assert "L-200" in tab.text and "History" in tab.text

    deleted = await client.post(f"{detail_url}/delete", data={"scope": "all"})
    assert deleted.status_code == 303 and deleted.headers["location"] == f"/drivers/{driver.id}#licences"
    assert await db_session.scalar(select(func.count()).select_from(DriverDocument)) == 0
    assert entry_id


@pytest.mark.asyncio
async def test_document_detail_scans_and_numbers_per_role(client, db_session, fleet_manager_user, viewer_user):
    driver = await make_driver(db_session)
    entry = await add_entry(db_session, driver, "id_card", number="555566667777", end=TODAY + timedelta(days=400))
    await login(client, "testviewer")
    page = await client.get(f"/driver-documents/{entry.id}")
    assert page.status_code == 200 and "555566667777" not in page.text and "•••• 7777" in page.text
    assert "/documents/gallery/" not in page.text and "/renew" not in page.text
    assert (await client.get(f"/documents/gallery/driver_document/{entry.id}")).status_code == 403
    assert (await client.get(f"/documents/gallery/driver/{driver.id}")).status_code == 403
    assert (await client.get(f"/driver-documents/{uuid4()}")).status_code == 302

    await client.get("/logout")
    await login(client, "testmanager")
    assert "555566667777" in (await client.get(f"/driver-documents/{entry.id}")).text
    assert (await client.get(f"/documents/gallery/driver_document/{entry.id}")).status_code == 200


@pytest.mark.asyncio
async def test_list_badges_filter_and_dashboard_widget(client, db_session, fleet_manager_user):
    a = await make_driver(db_session, "Има изтекъл")
    b = await make_driver(db_session, "Изтича скоро")
    c = await make_driver(db_session, "Всичко е наред")
    await add_entry(db_session, a, "id_card", end=TODAY - timedelta(days=3))
    await add_entry(db_session, a, "driving_licence", end=TODAY + timedelta(days=5), categories="B")
    await add_entry(db_session, b, "id_card", end=TODAY + timedelta(days=9))
    await add_entry(db_session, c, "id_card", end=None)
    await login(client, "testmanager")

    page = await client.get("/drivers")
    assert page.status_code == 200
    assert "1 expired" in page.text and "1 expiring soon" in page.text and "Documents" in page.text
    for removed in ("License", "Employee ID"):
        assert removed not in page.text

    expired = await client.get("/drivers", params={"doc_status": "expired"})
    assert "Има изтекъл" in expired.text and "Изтича скоро" not in expired.text and "Всичко е наред" not in expired.text
    soon = await client.get("/drivers", params={"doc_status": "expiring_soon"})
    assert "Има изтекъл" in soon.text and "Изтича скоро" in soon.text and "Всичко е наред" not in soon.text
    ignored = await client.get("/drivers", params={"doc_status": "bogus"})
    assert ignored.status_code == 200 and "Всичко е наред" in ignored.text

    widget = await client.get("/widgets/attention-needed")
    assert "Expired driver documents" in widget.text and "/drivers?doc_status=expired" in widget.text
    assert "Expiring driver documents" in widget.text and "/drivers?doc_status=expiring_soon" in widget.text


@pytest.mark.asyncio
async def test_driver_role_list_hides_document_state(client, db_session):
    me_user, _ = await make_user(db_session, UserRole.DRIVER)
    other = await make_driver(db_session, "Other")
    await add_entry(db_session, other, "id_card", end=TODAY - timedelta(days=3))
    await login(client, me_user.username)
    page = await client.get("/drivers")
    assert page.status_code == 200 and "1 expired" not in page.text and 'name="doc_status"' not in page.text


@pytest.mark.asyncio
async def test_bulgarian_labels(client, db_session, fleet_manager_user):
    driver = await make_driver(db_session)
    await add_entry(db_session, driver, "id_card", end=None)
    await login(client, "testmanager")
    await client.get("/set-language/bg")
    tab = await client.get(f"/drivers/{driver.id}/tab/licences")
    assert "Лична карта" in tab.text and "Безсрочен" in tab.text and "Свидетелство за управление" in tab.text
    page = await client.get(f"/drivers/{driver.id}")
    assert "Лицензи и документи" in page.text and "Качени документи" in page.text and "Общи данни" in page.text


@pytest.mark.asyncio
async def test_terminated_drivers_hidden_by_default(client, db_session, fleet_manager_user):
    from app.models.driver import DriverStatus

    await make_driver(db_session, "Работи")
    leave = await make_driver(db_session, "В отпуск")
    leave.status = DriverStatus.ON_LEAVE
    gone = await make_driver(db_session, "Напуснал")
    gone.status = DriverStatus.TERMINATED
    await db_session.flush()
    await login(client, "testmanager")

    default = (await client.get("/drivers")).text
    assert "Работи" in default and "В отпуск" in default and "Напуснал" not in default
    assert "Active and on leave" in default and "All (incl. terminated)" in default
    everyone = (await client.get("/drivers", params={"status": "all"})).text
    assert "Работи" in everyone and "Напуснал" in everyone
    only_gone = (await client.get("/drivers", params={"status": "terminated"})).text
    assert "Напуснал" in only_gone and "Работи" not in only_gone
    only_leave = (await client.get("/drivers", params={"status": "on_leave"})).text
    assert "В отпуск" in only_leave and "Работи" not in only_leave


@pytest.mark.asyncio
async def test_licence_categories_are_checkboxes(client, db_session, fleet_manager_user):
    driver = await make_driver(db_session)
    await login(client, "testmanager")
    form = await client.get("/driver-documents/new", params={"driver_id": str(driver.id)})
    for option in ("B", "C", "CE"):
        assert f'name="categories" value="{option}"' in form.text
    assert 'name="categories" value="D"' not in form.text and 'type="text" name="categories"' not in form.text

    created = await client.post(
        "/driver-documents/new",
        data={
            "driver_id": str(driver.id),
            "document_type": "driving_licence",
            "document_number": "L-1",
            "categories": ["CE", "B"],
        },
    )
    assert created.status_code == 302
    detail_url = created.headers["location"]
    assert "B, CE" in (await client.get(detail_url)).text
    edit = await client.get(f"{detail_url}/edit")
    import re

    checked = re.findall(r'<input type="checkbox" name="categories" value="(\w+)"[^>]*\bchecked\b', edit.text)
    assert checked == ["B", "CE"]

    bad = await client.post(
        "/driver-documents/new",
        data={"driver_id": str(driver.id), "document_type": "driving_licence", "document_number": "L-2", "categories": ["D"]},
    )
    assert bad.status_code == 422 and "Allowed categories: B, C, CE" in bad.text


@pytest.mark.asyncio
async def test_tab_alert_count_has_no_background(client, db_session, fleet_manager_user):
    driver = await make_driver(db_session)
    await add_entry(db_session, driver, "id_card", end=TODAY - timedelta(days=1))
    await login(client, "testmanager")
    page = (await client.get(f"/drivers/{driver.id}")).text
    assert "min-w-5" not in page and "bg-red-100 text-red-800\">1<" not in page
