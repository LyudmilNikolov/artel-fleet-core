"""QA #24: photos open in an in-page viewer; other files keep opening in a new tab; downloads save the file."""

import html
import json
import re
from uuid import uuid4

import pytest

from app.models.document import Document, DocumentType, EntityType
from tests.test_web.test_expenses import _login, _vehicle


class Storage:
    def __init__(self):
        self.calls = []

    def get_presigned_url(self, key, **kwargs):
        self.calls.append((key, kwargs))
        return f"http://files.test/{key}" + ("?dl=1" if kwargs.get("attachment") else "")

    def delete_file(self, key):
        pass


@pytest.fixture
def storage(monkeypatch):
    fake = Storage()
    monkeypatch.setattr("app.services.document_service.get_s3_client", lambda: fake)
    return fake


async def _add(db, vehicle, name, mime, kind=DocumentType.PHOTO):
    doc = Document(
        entity_type=EntityType.VEHICLE,
        entity_id=vehicle.id,
        type=kind,
        filename=name,
        s3_key=f"vehicle/{uuid4()}-{name}",
        mime_type=mime,
        size_bytes=2048,
    )
    db.add(doc)
    await db.flush()
    return doc


@pytest.mark.asyncio
async def test_photos_open_in_the_viewer_and_pdfs_in_a_new_tab(client, db_session, fleet_manager_user, storage):
    await _login(client)
    truck = await _vehicle(db_session)
    first = await _add(db_session, truck, "front.jpg", "image/jpeg")
    second = await _add(db_session, truck, "side.png", "image/png")
    pdf = await _add(db_session, truck, "act.pdf", "application/pdf", DocumentType.SCAN)
    await db_session.commit()

    page = (await client.get(f"/documents/gallery/vehicle/{truck.id}?lang=en")).text
    state = json.loads(html.unescape(re.search(r'x-data="\{ images: (\[.*?\]), open: false', page, re.S).group(1)))
    assert sorted(d["id"] for d in state) == sorted([str(first.id), str(second.id)])  # photos only, no pdf
    assert all(d["url"].startswith("http://files.test/") for d in state)
    assert f"@click.prevent=\"show('{first.id}')\"" in page and f"@click.prevent=\"show('{pdf.id}')\"" not in page
    assert f'href="/documents/{pdf.id}/download" target="_blank"' in page  # the pdf still opens in a new tab
    assert 'role="dialog"' in page and "Photo viewer" in page and "Next photo" in page and "Download" in page
    assert "?attachment=1" in page and "@keydown.escape.window" in page
    bg = (await client.get(f"/documents/gallery/vehicle/{truck.id}?lang=bg")).text
    assert "Следваща снимка" in bg and "Изтегли" in bg


@pytest.mark.asyncio
async def test_no_viewer_without_photos(client, db_session, fleet_manager_user, storage):
    await _login(client)
    truck = await _vehicle(db_session)
    await _add(db_session, truck, "act.pdf", "application/pdf", DocumentType.SCAN)
    await db_session.commit()
    page = (await client.get(f"/documents/gallery/vehicle/{truck.id}")).text
    assert "images: []" in page and "@click.prevent" not in page


@pytest.mark.asyncio
async def test_attachment_download_asks_storage_for_a_save_dialog(client, db_session, fleet_manager_user, storage):
    await _login(client)
    truck = await _vehicle(db_session)
    photo = await _add(db_session, truck, "front.jpg", "image/jpeg")
    await db_session.commit()
    shown = await client.get(f"/documents/{photo.id}/download", follow_redirects=False)
    assert shown.status_code == 302 and not shown.headers["location"].endswith("?dl=1")
    saved = await client.get(f"/documents/{photo.id}/download?attachment=1", follow_redirects=False)
    assert saved.headers["location"].endswith("?dl=1") and storage.calls[-1][1].get("attachment") is True


def test_presigned_attachment_sets_the_content_disposition():
    from app.utils.s3 import S3Client

    seen = {}

    class Signer:
        def generate_presigned_url(self, op, Params, ExpiresIn):
            seen.update(Params)
            return "http://x"

    client = S3Client.__new__(S3Client)
    client.bucket, client._presign_client = "b", Signer()
    client.get_presigned_url("k", filename="a b.jpg", mime_type="image/jpeg")
    assert "ResponseContentDisposition" not in seen  # photos are shown inline
    seen.clear()
    client.get_presigned_url("k", filename="a b.jpg", mime_type="image/jpeg", attachment=True)
    assert seen["ResponseContentDisposition"].startswith("attachment; filename*=UTF-8''a%20b.jpg")
