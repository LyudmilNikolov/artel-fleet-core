"""CompanyLicenceService on PostgreSQL: current entry, computed status, renewal, deletion."""

from datetime import date, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.models.company_licence import CompanyLicence
from app.models.document import Document, DocumentType, EntityType
from app.schemas.company_licence import CompanyLicenceInput
from app.services.company_licence_service import CompanyLicenceService
from app.services.errors import DomainError

TODAY = date(2026, 11, 3)


class FakeS3:
    def __init__(self):
        self.deleted = []

    def delete_file(self, key):
        self.deleted.append(key)


@pytest.fixture
def s3(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr("app.services.company_licence_service.get_s3_client", lambda: fake)
    return fake


def data(start=date(2020, 5, 14), end=date(2030, 5, 13), number="22098", **extra):
    return CompanyLicenceInput(
        licence_number=number, holder='"ЕВРОУЕЙ СЪРВИЗ" ООД', start_date=start, end_date=end, **extra
    )


def service(db, today=TODAY):
    return CompanyLicenceService(db, today=today)


@pytest.mark.asyncio
async def test_no_licence_means_no_current_and_no_alert(db_session):
    assert await service(db_session).current() is None
    assert await service(db_session).alert() is None


@pytest.mark.asyncio
async def test_create_computes_status_and_is_current(db_session):
    view = await service(db_session).create(data(), None)
    assert view.licence_number == "22098" and view.is_current
    assert view.status == "valid" and view.days_left == (date(2030, 5, 13) - TODAY).days
    assert await service(db_session).alert() is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "end,status",
    [
        (TODAY - timedelta(days=1), "expired"),
        (TODAY, "expiring_soon"),
        (TODAY + timedelta(days=30), "expiring_soon"),
        (TODAY + timedelta(days=31), "valid"),
    ],
)
async def test_status_boundaries_and_alert(db_session, end, status):
    await service(db_session).create(data(start=date(2020, 1, 1), end=end), None)
    current = await service(db_session).current()
    assert current.status == status
    assert (await service(db_session).alert() is not None) == (status != "valid")


@pytest.mark.asyncio
async def test_latest_end_date_is_current_and_older_is_history(db_session):
    old = await service(db_session).create(data(end=date(2025, 5, 13), number="OLD"), None)
    new = await service(db_session).create(data(number="NEW"), None, renewed_from_id=old.id)
    entries = await service(db_session).list_entries()
    assert [e.licence_number for e in entries] == ["NEW", "OLD"]
    assert [e.is_current for e in entries] == [True, False]
    assert new.entry.renewed_from_id == old.id
    # the expired old entry is history and never raises the alert
    assert await service(db_session).alert() is None


@pytest.mark.asyncio
async def test_renew_with_unknown_source_is_rejected(db_session):
    from uuid import uuid4

    with pytest.raises(DomainError, match="error.company_licence_not_found"):
        await service(db_session).create(data(), None, renewed_from_id=uuid4())


@pytest.mark.asyncio
async def test_renewal_proposal_keeps_number_and_holder_and_moves_dates(db_session):
    view = await service(db_session).create(data(), None)
    proposal = service(db_session).renewal_proposal(view)
    assert proposal["licence_number"] == "22098" and proposal["holder"] == '"ЕВРОУЕЙ СЪРВИЗ" ООД'
    assert proposal["start_date"] == date(2030, 5, 14) and proposal["end_date"] == date(2040, 5, 13)


@pytest.mark.asyncio
async def test_update_corrects_an_entry(db_session):
    view = await service(db_session).create(data(), None)
    updated = await service(db_session).update(view.id, data(number="22099", notes="  corrected  "))
    assert updated.licence_number == "22099" and updated.notes == "corrected"
    with pytest.raises(DomainError):
        from uuid import uuid4

        await service(db_session).update(uuid4(), data())


@pytest.mark.parametrize(
    "overrides",
    [
        {"licence_number": "   "},
        {"holder": ""},
        {"start_date": date(2030, 5, 14), "end_date": date(2030, 5, 13)},
    ],
)
def test_input_validation(overrides):
    values = {
        "licence_number": "1",
        "holder": "Firm",
        "start_date": date(2020, 1, 1),
        "end_date": date(2021, 1, 1),
        **overrides,
    }
    with pytest.raises(ValidationError):
        CompanyLicenceInput(**values)


def test_input_trims_and_blanks_become_none():
    parsed = data(issued_by="  ", notes="  ", number="  22098 ")
    assert parsed.licence_number == "22098" and parsed.issued_by is None and parsed.notes is None


@pytest.mark.asyncio
async def test_delete_removes_scans_and_makes_previous_current(db_session, s3):
    old = await service(db_session).create(data(end=date(2025, 5, 13), number="OLD"), None)
    new = await service(db_session).create(data(number="NEW"), None, renewed_from_id=old.id)
    db_session.add(
        Document(
            entity_type=EntityType.COMPANY_LICENCE,
            entity_id=new.id,
            type=DocumentType.LICENSE,
            filename="licence.pdf",
            s3_key="company_licence/x/licence.pdf",
            mime_type="application/pdf",
            size_bytes=10,
        )
    )
    await db_session.flush()
    await service(db_session).delete(new.id)
    assert s3.deleted == ["company_licence/x/licence.pdf"]
    assert await db_session.scalar(select(Document).where(Document.entity_id == new.id)) is None
    assert (await service(db_session).current()).licence_number == "OLD"
    with pytest.raises(DomainError):
        await service(db_session).delete(new.id)


@pytest.mark.asyncio
async def test_deleting_the_source_clears_the_renewal_link(db_session, s3):
    old = await service(db_session).create(data(end=date(2025, 5, 13), number="OLD"), None)
    new = await service(db_session).create(data(number="NEW"), None, renewed_from_id=old.id)
    await service(db_session).delete(old.id)
    row = await db_session.get(CompanyLicence, new.id)
    assert row.renewed_from_id is None
