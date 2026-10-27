"""Bell, inbox, item page and dashboard counts for documents that need attention (phase 1 of notifications)."""

import re
from datetime import timedelta
from uuid import uuid4

import pytest

from app.models.notification import Notification, NotificationType
from app.schemas.compliance import ComplianceCreate
from app.schemas.driver import DriverCreate
from app.schemas.driver_document import DriverDocumentCreate
from app.schemas.vehicle import VehicleCreate
from app.services.compliance_service import ComplianceService
from app.services.driver_document_service import DriverDocumentService
from app.services.driver_service import DriverService
from app.services.validity import local_today
from app.services.vehicle_service import VehicleService
from tests.test_web.test_driver_access import make_driver_login, make_vehicle

TODAY = local_today()
SECRET_NUMBER = "SECRET-DOC-NO-4711"


async def login(client, username, password="testpass123"):
    response = await client.post("/login", data={"username": username, "password": password})
    assert response.status_code == 302


async def vehicle(db, plate):
    return await VehicleService(db).create(
        VehicleCreate(
            vehicle_type="tractor_unit", license_plate=plate, brand="Volvo", model="FH", department="Test department"
        )
    )


async def vehicle_doc(db, v, kind, days):
    end = TODAY + timedelta(days=days)
    return await ComplianceService(db).create(
        ComplianceCreate(
            vehicle_id=v.id,
            document_type=kind,
            start_date=end - timedelta(days=365),
            end_date=end,
            policy_number=SECRET_NUMBER,
        ),
        None,
    )


async def driver_doc(db, d, kind, days):
    return await DriverDocumentService(db).create(
        DriverDocumentCreate(
            driver_id=d.id, document_type=kind, document_number=SECRET_NUMBER, end_date=TODAY + timedelta(days=days)
        ),
        None,
    )


async def new_driver(db, name):
    return await DriverService(db).create(DriverCreate(full_name=name))


def no_background(html):
    """The count is a plain red number: no badge background."""
    span = re.search(r'<span data-bell-count[^>]*class="([^"]*)"', html).group(1)
    return "text-red-600" in span and "bg-" not in span


def bell_state(html):
    count = re.search(r'data-bell-count="(\d+)"', html)
    tone = re.search(r'data-bell-tone="(\w+)"', html)
    return (int(count.group(1)) if count else None, tone.group(1) if tone else None)


@pytest.mark.asyncio
async def test_bell_sits_between_dark_mode_toggle_and_user_menu(client, admin_user):
    await login(client, "testadmin")
    page = (await client.get("/vehicles")).text
    toggle, bell, menu = page.index("Dark mode toggle"), page.index('id="notification-bell"'), page.index("User menu")
    assert toggle < bell < menu
    assert 'hx-get="/notifications/bell"' in page and "every 300s, bell-refresh" in page


@pytest.mark.asyncio
async def test_bell_requires_login(client):
    response = await client.get("/notifications/bell")
    assert response.status_code == 200 and response.text.strip() == ""


@pytest.mark.asyncio
async def test_bell_badge_colour_and_count(client, db_session, admin_user):
    await login(client, "testadmin")
    empty = (await client.get("/notifications/bell")).text
    assert bell_state(empty) == (None, None)  # nothing to show, but the bell itself is red
    assert "text-red-600" in empty

    v = await vehicle(db_session, "CA1000AA")
    await vehicle_doc(db_session, v, "casco", 12)
    soon = (await client.get("/notifications/bell")).text
    assert bell_state(soon) == (1, "expiring") and no_background(soon)

    await vehicle_doc(db_session, v, "civil_liability", -2)
    both = (await client.get("/notifications/bell")).text
    assert bell_state(both) == (2, "expired") and no_background(both)


@pytest.mark.asyncio
async def test_bell_for_every_role_and_driver_scope(client, db_session, admin_user, fleet_manager_user, viewer_user):
    mine, other = await new_driver(db_session, "Мой Водач"), await new_driver(db_session, "Чужд Водач")
    v_mine = await make_vehicle(db_session, "CB0001BB", driver=mine)
    v_other = await make_vehicle(db_session, "CB0002BB")
    await driver_doc(db_session, mine, "id_card", 3)
    await driver_doc(db_session, other, "id_card", 3)
    await vehicle_doc(db_session, v_mine, "casco", 3)
    await vehicle_doc(db_session, v_other, "casco", 3)

    for username in ("testadmin", "testmanager", "testviewer"):
        await login(client, username)
        assert bell_state((await client.get("/notifications/bell")).text)[0] == 4, username
        await client.post("/logout")

    await make_driver_login(db_session, mine, "mydriver")
    await login(client, "mydriver", "driver-pass-1")
    assert bell_state((await client.get("/notifications/bell")).text)[0] == 2
    dropdown = (await client.get("/notifications/dropdown")).text
    assert "CB0001BB" in dropdown and "Мой Водач" in dropdown
    assert "CB0002BB" not in dropdown and "Чужд Водач" not in dropdown


@pytest.mark.asyncio
async def test_dropdown_groups_links_and_never_shows_numbers(client, db_session, admin_user):
    v = await vehicle(db_session, "CA2000AA")
    d = await new_driver(db_session, "Георги Иванов")
    late = await vehicle_doc(db_session, v, "casco", -5)
    soon = await driver_doc(db_session, d, "driving_licence", 6)
    db_session.add(Notification(user_id=admin_user.id, title="t", message="m", type=NotificationType.SYSTEM))
    await db_session.flush()

    await login(client, "testadmin")
    html = (await client.get("/notifications/dropdown")).text
    assert html.index("Expired") < html.index("Expiring soon")
    assert f"/notifications/item/compliance_document/{late.id}" in html
    assert f"/notifications/item/driver_document/{soon.id}" in html
    assert "CA2000AA" in html and "Георги Иванов" in html
    assert SECRET_NUMBER not in html
    assert 'href="/notifications"' in html
    assert 'href="/notifications?tab=messages"' in html and "1 unread messages" in html


@pytest.mark.asyncio
async def test_inbox_lists_filters_and_sorts(client, db_session, admin_user):
    v = await vehicle(db_session, "CA3000AA")
    d = await new_driver(db_session, "Борис Борисов")
    await vehicle_doc(db_session, v, "casco", -1)
    await vehicle_doc(db_session, v, "civil_liability", 20)
    await driver_doc(db_session, d, "adr_certificate", 10)
    await vehicle_doc(db_session, v, "technical_inspection", 300)  # valid: not listed
    await login(client, "testadmin")

    table = {"HX-Request": "true", "HX-Target": "attention-table"}

    async def rows(**params):
        response = await client.get("/notifications", params=params, headers=table)
        assert "<html" not in response.text
        return response.text

    page = await rows()
    assert SECRET_NUMBER not in page and "Annual technical inspection" not in page
    order = [page.index(t) for t in ("CASCO insurance", "ADR certificate", "Civil liability insurance")]
    assert order == sorted(order)  # most urgent first

    only_expired = await rows(status="expired")
    assert "CASCO insurance" in only_expired and "ADR certificate" not in only_expired
    by_source = await rows(source="driver:adr_certificate")
    assert "ADR certificate" in by_source and "CASCO insurance" not in by_source
    by_search = await rows(q="борисов")
    assert "Борис Борисов" in by_search and "CA3000AA" not in by_search
    by_name = await rows(sort="subject")
    assert by_name.index("CA3000AA") < by_name.index("Борис Борисов")
    assert by_name.count("data-attention-row") == 3

    full = (await client.get("/notifications")).text  # the full page has the filter form around the table
    assert "<html" in full and 'name="q"' in full and SECRET_NUMBER not in full


@pytest.mark.asyncio
async def test_inbox_messages_tab_keeps_stored_notifications(client, db_session, admin_user):
    db_session.add(
        Notification(
            user_id=admin_user.id, title="Pending approval", message="Change waiting", type=NotificationType.SYSTEM
        )
    )
    await db_session.flush()
    await login(client, "testadmin")
    page = (await client.get("/notifications", params={"tab": "messages"})).text
    assert "Pending approval" in page and "Change waiting" in page
    assert "Pending approval" not in (await client.get("/notifications")).text


@pytest.mark.asyncio
async def test_item_page_previous_next_follows_the_list_and_filters(client, db_session, admin_user):
    v = await vehicle(db_session, "CA4000AA")
    first = await vehicle_doc(db_session, v, "casco", -3)
    second = await vehicle_doc(db_session, v, "civil_liability", 4)
    third = await vehicle_doc(db_session, v, "technical_inspection", 9)
    await login(client, "testadmin")

    def url(doc, **params):
        query = "&".join(f"{k}={val}" for k, val in params.items())
        return f"/notifications/item/compliance_document/{doc.id}" + (f"?{query}" if query else "")

    def nav(html, kind):
        match = re.search(rf'<(a|span) data-nav="{kind}"([^>]*)>', html)
        return match.group(1), match.group(2)

    page = (await client.get(url(second))).text
    assert nav(page, "previous")[0] == "a" and f'href="{url(first)}"' in nav(page, "previous")[1]
    assert nav(page, "next")[0] == "a" and f'href="{url(third)}"' in nav(page, "next")[1]
    assert "2 of 3" in page
    assert f"/compliance/{second.id}" in page and f"/vehicles/{v.id}" in page
    assert SECRET_NUMBER not in page

    start = (await client.get(url(first))).text
    assert "1 of 3" in start and nav(start, "previous")[0] == "span" and nav(start, "next")[0] == "a"
    end = (await client.get(url(third))).text
    assert "3 of 3" in end and nav(end, "next")[0] == "span"

    # the inbox filter travels with the links, so Previous / Next stay inside the same list
    filtered = (await client.get(url(second, status="expiring_soon"))).text
    assert "1 of 2" in filtered
    assert nav(filtered, "previous")[0] == "span"
    assert f'href="{url(third, status="expiring_soon")}"' in nav(filtered, "next")[1]
    assert url(first) not in filtered

    by_end_date = (await client.get(url(first, sort="end_date"))).text
    assert "1 of 3" in by_end_date and f'href="{url(second, sort="end_date")}"' in nav(by_end_date, "next")[1]


@pytest.mark.asyncio
async def test_item_page_redirects_for_valid_history_unknown_and_out_of_scope(client, db_session, admin_user):
    v = await vehicle(db_session, "CA5000AA")
    ok = await vehicle_doc(db_session, v, "casco", 400)  # valid, not an attention item
    mine = await new_driver(db_session, "Само Моят")
    other = await new_driver(db_session, "Само Чуждия")
    theirs = await driver_doc(db_session, other, "id_card", 2)
    await driver_doc(db_session, mine, "id_card", 2)
    await login(client, "testadmin")
    for path in (
        f"/notifications/item/compliance_document/{ok.id}",
        f"/notifications/item/compliance_document/{uuid4()}",
        f"/notifications/item/nonsense/{ok.id}",
    ):
        response = await client.get(path)
        assert response.status_code == 302 and response.headers["location"] == "/notifications", path
    assert (await client.get(f"/notifications/item/driver_document/{theirs.id}")).status_code == 200
    await client.post("/logout")

    await make_driver_login(db_session, mine, "scoped")
    await login(client, "scoped", "driver-pass-1")
    response = await client.get(f"/notifications/item/driver_document/{theirs.id}")
    assert response.status_code == 302 and response.headers["location"] == "/notifications"


@pytest.mark.asyncio
async def test_dashboard_widget_numbers_match_the_inbox_and_bell(client, db_session, fleet_manager_user):
    v = await vehicle(db_session, "CA6000AA")
    d = await new_driver(db_session, "Дашборд Водач")
    await vehicle_doc(db_session, v, "casco", -2)
    await vehicle_doc(db_session, v, "civil_liability", 5)
    await driver_doc(db_session, d, "id_card", -9)
    await driver_doc(db_session, d, "medical_certificate", 15)
    await driver_doc(db_session, d, "psycho_test", 25)
    await login(client, "testmanager")

    from app.services.dashboard_service import DashboardService

    stats = await DashboardService(db_session).attention_needed()
    assert (
        stats["expired_compliance"],
        stats["expiring_compliance"],
        stats["expired_driver_documents"],
        stats["expiring_driver_documents"],
    ) == (1, 1, 1, 2)
    inbox = (await client.get("/notifications")).text
    assert inbox.count("data-attention-row") == 5
    assert bell_state((await client.get("/notifications/bell")).text)[0] == 5
    widget = (await client.get("/widgets/attention-needed")).text
    assert 'href="/compliance?status=expired"' in widget and 'href="/drivers?doc_status=expiring_soon"' in widget


@pytest.mark.asyncio
async def test_pages_render_in_bulgarian_and_english(client, db_session, admin_user):
    v = await vehicle(db_session, "CA7000AA")
    doc = await vehicle_doc(db_session, v, "casco", -4)
    await login(client, "testadmin")
    bg = (await client.get("/notifications", params={"lang": "bg"})).text
    assert "Документи" in bg and "Каско" in bg and "просрочен с 4 дни" in bg
    en = (await client.get("/notifications", params={"lang": "en"})).text
    assert "CASCO insurance" in en and "4 days overdue" in en
    item_bg = (await client.get(f"/notifications/item/compliance_document/{doc.id}", params={"lang": "bg"})).text
    assert "Документ, изискващ внимание" in item_bg
    item_en = (await client.get(f"/notifications/item/compliance_document/{doc.id}", params={"lang": "en"})).text
    assert "Document needing attention" in item_en
    assert TODAY.strftime("%d.%m.%Y") not in item_en  # english uses d/m/Y


@pytest.mark.asyncio
async def test_dashboard_widget_total_is_a_plain_red_number(client, db_session, admin_user):
    v = await vehicle(db_session, "CA9900AA")
    await vehicle_doc(db_session, v, "casco", -1)
    await login(client, "testadmin")
    widget = (await client.get("/widgets/attention-needed")).text
    header = re.search(r'<span class="([^"]*)">\s*1\s*</span>', widget.split("<div class=\"space-y-3\">")[0]).group(1)
    assert "text-red-600" in header and "fc-badge" not in header and "bg-" not in header
