"""An assigned driver adds expenses, maintenance and files, only for the vehicle assigned to them."""


import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.compliance import ComplianceDocument
from app.models.document import Document, DocumentType, EntityType
from app.models.expense import Expense
from app.models.maintenance import MaintenanceRecord
from app.services.document_service import DocumentService
from tests.test_web.test_driver_access import get, login, world  # noqa: F401
from tests.test_web.test_driver_self_service import FakeS3

EXPENSE = {"category": "parts", "amount": "25.50", "date": "2026-01-05", "description": "Wiper blades"}
MAINTENANCE = {"type": "repair", "title": "Flat tire", "status": "scheduled", "scheduled_date": "2026-02-01"}


@pytest.fixture
def s3(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr("app.services.document_service.get_s3_client", lambda: fake)
    return fake


async def count(db, model, **where):
    query = select(model)
    for key, value in where.items():
        query = query.where(getattr(model, key) == value)
    return len(list((await db.scalars(query)).all()))


# --- expenses ----------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_driver_adds_an_expense_for_their_own_vehicle(client, db_session, world):  # noqa: F811
    va = world["vehicles"]["a"]
    await login(client, "driver_a")
    listing = await client.get("/expenses")
    assert 'href="/expenses/new"' in listing.text  # the Add button is there
    form = await client.get("/expenses/new")
    assert form.status_code == 200 and str(va.id) in form.text  # their only vehicle is preselected
    assert "CB2222BB" not in form.text and 'value="casco"' not in form.text  # nothing of other vehicles / documents
    options = await client.get("/expenses/vehicle-options")
    assert "CA1111AA" in options.text and "CB2222BB" not in options.text
    created = await client.post("/expenses/new", data={**EXPENSE, "vehicle_id": str(va.id)}, follow_redirects=False)
    assert created.status_code == 303
    expense = await db_session.scalar(select(Expense).where(Expense.description == "Wiper blades"))
    assert expense.vehicle_id == va.id and expense.driver_id == world["drivers"]["a"].id
    assert expense.created_by == world["users"]["a"].id
    audit = await db_session.scalar(select(AuditLog).where(AuditLog.entity_id == expense.id))
    assert audit.user_id == world["users"]["a"].id
    assert (await client.get(f"/expenses/{expense.id}")).status_code == 200


@pytest.mark.asyncio
async def test_driver_cannot_add_expenses_for_other_vehicles_or_documents(client, db_session, world):  # noqa: F811
    va, vb = world["vehicles"]["a"], world["vehicles"]["b"]
    await login(client, "driver_a")
    before = await count(db_session, Expense)
    other = await client.post("/expenses/new", data={**EXPENSE, "vehicle_id": str(vb.id)})
    assert other.status_code in (400, 403, 422) and await count(db_session, Expense) == before
    # insurance / inspection documents stay with managers, also when sent as an expense
    docs_before = await count(db_session, ComplianceDocument)
    casco = await client.post(
        "/expenses/new",
        data={
            **EXPENSE,
            "category": "casco",
            "vehicle_id": str(va.id),
            "valid_from": "2026-01-01",
            "valid_until": "2027-01-01",
        },
    )
    assert casco.status_code in (400, 403, 422)
    assert await count(db_session, Expense) == before and await count(db_session, ComplianceDocument) == docs_before
    # and no editing or deleting of any expense, their own included
    expense = world["records"]["a"][0]
    own = f"/drivers/{world['drivers']['a'].id}"
    assert (await get(client, f"/expenses/{expense.id}/edit")).headers["location"] == own
    deleted = await client.post(f"/expenses/{expense.id}/delete", follow_redirects=False)
    assert deleted.status_code in (302, 303) and deleted.headers["location"] == own
    assert await db_session.get(Expense, expense.id) is not None


# --- maintenance -------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_driver_adds_maintenance_for_their_own_vehicle_only(client, db_session, world):  # noqa: F811
    va, vb = world["vehicles"]["a"], world["vehicles"]["b"]
    await login(client, "driver_a")
    assert 'href="/maintenance/new"' in (await client.get("/maintenance")).text
    form = await client.get("/maintenance/new")
    assert form.status_code == 200 and str(va.id) in form.text and "CB2222BB" not in form.text
    options = await client.get("/maintenance/vehicle-options")
    assert "CA1111AA" in options.text and "CB2222BB" not in options.text
    created = await client.post(
        "/maintenance/new", data={**MAINTENANCE, "vehicle_id": str(va.id)}, follow_redirects=False
    )
    assert created.status_code == 303
    record = await db_session.scalar(select(MaintenanceRecord).where(MaintenanceRecord.title == "Flat tire"))
    assert record.vehicle_id == va.id and record.created_by == world["users"]["a"].id
    before = await count(db_session, MaintenanceRecord)
    other = await client.post(
        "/maintenance/new", data={**MAINTENANCE, "title": "Other truck", "vehicle_id": str(vb.id)}
    )
    assert other.status_code in (400, 403, 422) and await count(db_session, MaintenanceRecord) == before
    # completing, editing and deleting stay with managers
    for method, path in (
        ("get", f"/maintenance/{record.id}/edit"),
        ("post", f"/maintenance/{record.id}/complete"),
        ("post", f"/maintenance/{record.id}/delete"),
    ):
        response = await getattr(client, method)(path, follow_redirects=False)
        assert response.status_code in (302, 303), path
    assert await db_session.get(MaintenanceRecord, record.id) is not None


# --- files -------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_driver_uploads_files_for_their_vehicle_records_only(client, db_session, world, s3):  # noqa: F811
    va, vb = world["vehicles"]["a"], world["vehicles"]["b"]
    exp_a, rec_a, doc_a = world["records"]["a"]
    exp_b, rec_b, doc_b = world["records"]["b"]
    ids = {
        "va": va.id,
        "vb": vb.id,
        "exp_a": exp_a.id,
        "exp_b": exp_b.id,
        "rec_a": rec_a.id,
        "rec_b": rec_b.id,
        "doc_a": doc_a.id,
        "doc_b": doc_b.id,
    }
    db_session.expunge_all()  # pages load their records fresh, as each request does in production
    va_id, exp_a_id, rec_a_id, doc_a_id = ids["va"], ids["exp_a"], ids["rec_a"], ids["doc_a"]
    await login(client, "driver_a")
    # the upload boxes are shown on the pages of their own vehicle
    for path in (f"/vehicles/{va_id}", f"/expenses/{exp_a_id}", f"/maintenance/{rec_a_id}", f"/compliance/{doc_a_id}"):
        assert 'hx-post="/documents/upload"' in (await client.get(path)).text, path

    files = {"file": ("receipt.pdf", b"%PDF-1.4 x", "application/pdf")}

    async def upload(kind, entity_id):
        return await client.post(
            "/documents/upload",
            files=files,
            data={"entity_type": kind, "entity_id": str(entity_id), "doc_type": "invoice"},
        )

    for kind, entity in (
        ("vehicle", va.id),
        ("expense", exp_a.id),
        ("maintenance", rec_a.id),
        ("compliance_document", doc_a.id),
    ):
        response = await upload(kind, entity)
        assert response.status_code == 200 and "receipt.pdf" in response.text, kind
    assert len(s3.objects) == 4
    for kind, entity in (
        ("vehicle", vb.id),
        ("expense", exp_b.id),
        ("maintenance", rec_b.id),
        ("compliance_document", doc_b.id),
    ):
        assert (await upload(kind, entity)).status_code == 403, kind
    assert len(s3.objects) == 4
    docs = list((await db_session.scalars(select(Document))).all())
    assert len(docs) == 4 and all(d.uploaded_by is not None for d in docs)
    # they can remove a file they uploaded, but not one a manager uploaded
    mine = next(d for d in docs if d.entity_type == EntityType.EXPENSE)
    assert (await client.delete(f"/documents/{mine.id}")).status_code == 200
    manager_file = await DocumentService(db_session).upload(
        file_data=b"%PDF-1.4",
        filename="manager.pdf",
        mime_type="application/pdf",
        entity_type=EntityType.VEHICLE,
        entity_id=va_id,
        doc_type=DocumentType.OTHER,
        uploaded_by=None,
    )
    await db_session.commit()
    assert (await client.delete(f"/documents/{manager_file.id}")).status_code == 403


# --- who is not affected -----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_driver_without_a_vehicle_and_other_roles_cannot_contribute(client, db_session, world, viewer_user, s3):  # noqa: F811
    va = world["vehicles"]["a"]
    await login(client, "driver_none")
    for path in ("/expenses/new", "/maintenance/new"):
        assert (await get(client, path)).headers["location"] == f"/drivers/{world['drivers']['none'].id}"
    files = {"file": ("x.pdf", b"%PDF-1.4", "application/pdf")}
    denied = await client.post(
        "/documents/upload", files=files, data={"entity_type": "vehicle", "entity_id": str(va.id), "doc_type": "other"}
    )
    assert denied.status_code == 403
    await client.get("/logout")
    assert (await client.post("/login", data={"username": "testviewer", "password": "testpass123"})).status_code == 302
    for path in ("/expenses/new", "/maintenance/new"):
        assert (await get(client, path)).headers["location"] == "/"
    assert 'href="/expenses/new"' not in (await client.get("/expenses")).text
    denied = await client.post(
        "/documents/upload", files=files, data={"entity_type": "vehicle", "entity_id": str(va.id), "doc_type": "other"}
    )
    assert denied.status_code == 403
    assert not s3.objects
