"""Web UI for the company licence: forms, renewal, roles, dashboard warning, scans."""

from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.models.audit_log import AuditLog
from app.models.company_licence import CompanyLicence
from app.models.document import EntityType
from app.schemas.company_licence import CompanyLicenceInput
from app.services.company_licence_service import CompanyLicenceService
from app.services.document_service import DocumentService
from app.services.errors import DomainError
from tests.test_web.test_driver_access import make_driver, make_driver_login

PAGE = "/company-licence"


async def login(client, username, password="testpass123"):
    response = await client.post("/login", data={"username": username, "password": password})
    assert response.status_code == 302


def form(**over):
    values = {
        "licence_number": "22098",
        "holder": '"ЕВРОУЕЙ СЪРВИЗ" ООД',
        "issued_by": "",
        "start_date": "2020-05-14",
        "end_date": "2030-05-13",
        "notes": "",
    }
    return {**values, **over}


async def stored(db):
    return list((await db.scalars(select(CompanyLicence).order_by(CompanyLicence.end_date))).all())


@pytest.mark.asyncio
async def test_empty_page_invites_to_add(client, fleet_manager_user):
    await login(client, "testmanager")
    page = await client.get(PAGE)
    assert page.status_code == 200 and "No company licence has been entered yet." in page.text
    assert f'href="{PAGE}/new"' in page.text


@pytest.mark.asyncio
async def test_manager_creates_renews_edits_and_deletes(client, db_session, fleet_manager_user):
    await login(client, "testmanager")
    assert (await client.get(f"{PAGE}/new")).status_code == 200
    response = await client.post(f"{PAGE}/new", data=form())
    assert response.status_code == 302 and response.headers["location"] == PAGE
    [entry] = await stored(db_session)
    page = (await client.get(PAGE)).text
    assert "22098" in page and "ЕВРОУЕЙ СЪРВИЗ" in page and f"{PAGE}/{entry.id}/renew" in page
    assert f"/documents/gallery/company_licence/{entry.id}" in page  # scan area
    assert page.index("fc-badge-success") < page.index("Every tractor unit carries")  # explanation under the status

    renew_form = await client.get(f"{PAGE}/{entry.id}/renew")
    assert renew_form.status_code == 200 and 'value="2030-05-14"' in renew_form.text
    response = await client.post(
        f"{PAGE}/{entry.id}/renew", data=form(licence_number="30001", start_date="2030-05-14", end_date="2040-05-13")
    )
    assert response.status_code == 302
    old, new = await stored(db_session)
    assert new.licence_number == "30001" and new.renewed_from_id == old.id
    page = (await client.get(PAGE)).text
    assert "30001" in page and "Replaced" in page  # old entry is shown as history

    response = await client.post(f"{PAGE}/{new.id}/edit", data=form(licence_number="30002", end_date="2040-05-13", start_date="2030-05-14"))
    assert response.status_code == 302
    await db_session.refresh(new)
    assert new.licence_number == "30002"

    response = await client.post(f"{PAGE}/{new.id}/delete")
    assert response.status_code == 303
    assert [e.licence_number for e in await stored(db_session)] == ["22098"]


@pytest.mark.asyncio
async def test_invalid_form_shows_errors_and_saves_nothing(client, db_session, fleet_manager_user):
    await login(client, "testmanager")
    response = await client.post(f"{PAGE}/new", data=form(licence_number="", end_date="2020-01-01"))
    assert response.status_code == 422
    assert await db_session.scalar(select(func.count()).select_from(CompanyLicence)) == 0
    # what was typed is kept
    assert "ЕВРОУЕЙ СЪРВИЗ" in response.text


@pytest.mark.asyncio
async def test_bulgarian_labels(client, db_session, fleet_manager_user):
    await login(client, "testmanager")
    page = await client.get(PAGE, params={"lang": "bg"})
    assert "Лиценз на фирмата" in page.text


@pytest.mark.asyncio
async def test_viewer_reads_but_cannot_change(client, db_session, viewer_user):
    await CompanyLicenceService(db_session).create(CompanyLicenceInput(**form()), None)
    await login(client, "testviewer")
    page = await client.get(PAGE)
    assert page.status_code == 200 and "22098" in page.text
    entry = (await stored(db_session))[0]
    for forbidden in (f"{PAGE}/new", f"{PAGE}/{entry.id}/renew", f"{PAGE}/{entry.id}/edit", f"{PAGE}/{entry.id}/delete"):
        assert forbidden not in page.text, forbidden
    for path in (f"{PAGE}/new", f"{PAGE}/{entry.id}/edit", f"{PAGE}/{entry.id}/renew"):
        response = await client.get(path)
        assert response.status_code in (302, 303) and response.headers["location"] == "/", path
    assert (await client.post(f"{PAGE}/new", data=form(licence_number="X"))).status_code in (302, 303)
    assert (await client.post(f"{PAGE}/{entry.id}/delete")).status_code in (302, 303)
    assert [e.licence_number for e in await stored(db_session)] == ["22098"]


@pytest.mark.asyncio
async def test_driver_is_sent_away(client, db_session):
    driver = await make_driver(db_session, "Шофьор Иван")
    await make_driver_login(db_session, driver, "wheel")
    await login(client, "wheel", "driver-pass-1")
    response = await client.get(PAGE)
    assert response.status_code in (302, 303) and not response.headers["location"].startswith(PAGE)


@pytest.mark.asyncio
async def test_dashboard_warns_when_the_licence_expires_soon_or_is_expired(client, db_session, fleet_manager_user):
    await login(client, "testmanager")
    assert "the company licence" not in (await client.get("/")).text.lower()  # none entered: no warning
    today = date.today()
    entry = await CompanyLicenceService(db_session).create(
        CompanyLicenceInput(**form(start_date="2020-01-01", end_date=(today + timedelta(days=10)).isoformat())), None
    )
    assert "the company licence 22098 expires" in (await client.get("/")).text.lower()
    row = await db_session.get(CompanyLicence, entry.id)
    row.end_date = today - timedelta(days=1)
    await db_session.flush()
    assert "the company licence 22098 expired" in (await client.get("/")).text.lower()
    row.end_date = today + timedelta(days=400)
    await db_session.flush()
    assert "the company licence" not in (await client.get("/")).text.lower()


@pytest.mark.asyncio
async def test_changes_are_audited(client, db_session, fleet_manager_user):
    await login(client, "testmanager")
    await client.post(f"{PAGE}/new", data=form())
    row = await db_session.scalar(select(AuditLog).where(AuditLog.entity_type == "company_licence"))
    assert row is not None and row.action.value == "create"


@pytest.mark.asyncio
async def test_scans_attach_only_to_an_existing_licence(db_session):
    from uuid import uuid4

    with pytest.raises(DomainError, match="error.company_licence_not_found"):
        await DocumentService(db_session).upload(
            file_data=b"%PDF-1.4 x",
            filename="a.pdf",
            mime_type="application/pdf",
            entity_type=EntityType.COMPANY_LICENCE,
            entity_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_scan_upload_is_not_tagged_as_a_driving_licence(client, db_session, admin_user):
    """Regression: the scan used the `license` type, whose label is "driving licence"."""
    from app.services.company_licence_service import CompanyLicenceService
    from app.schemas.company_licence import CompanyLicenceInput
    from datetime import date, timedelta

    today = date.today()
    await CompanyLicenceService(db_session).create(
        CompanyLicenceInput(
            licence_number="1", holder="Test", start_date=today - timedelta(days=5), end_date=today + timedelta(days=300)
        ),
        admin_user.id,
    )
    await db_session.commit()
    await client.post("/login", data={"username": "testadmin", "password": "testpass123"})
    page = await client.get("/company-licence")
    assert 'name="doc_type" value="scan"' in page.text and 'value="license"' not in page.text
