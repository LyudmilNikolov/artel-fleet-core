"""Uploads are validated by content, with safe names, a size limit and attachment downloads for Office files."""

import io
import zipfile
from uuid import uuid4

import pytest

from app.models.document import EntityType
from app.services.document_service import DocumentService
from app.services.errors import DomainError
from app.utils.s3 import S3Client
from app.utils.upload_validation import (
    DOCX,
    XLSX,
    check_content,
    detect_mime,
    read_limited,
    sanitize_filename,
)

PDF = b"%PDF-1.7\n1 0 obj\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 20
GIF = b"GIF89a" + b"\x00" * 20
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 "
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 20


def office(root: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<x/>")
        archive.writestr(f"{root}/document.xml", "<x/>")
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (PDF, "application/pdf"),
        (PNG, "image/png"),
        (JPEG, "image/jpeg"),
        (GIF, "image/gif"),
        (WEBP, "image/webp"),
        (OLE, "application/msword"),
        (office("word"), DOCX),
        (office("xl"), XLSX),
        (b"MZ\x90\x00 program", None),
        (b"<html><script>alert(1)</script>", None),
        (b"#!/bin/sh\nrm -rf /", None),
        (b"PK\x03\x04 broken zip", None),
        (office("ppt"), None),
        (b"", None),
    ],
)
def test_detect_mime(data, expected):
    assert detect_mime(data) == expected


def test_declared_type_must_match_the_content():
    assert check_content("application/pdf", PDF) == "application/pdf"
    assert check_content("application/vnd.ms-excel", OLE) == "application/vnd.ms-excel"
    assert check_content(XLSX, office("xl")) == XLSX
    for declared, data in (
        ("application/pdf", b"MZ\x90\x00 program"),
        ("image/png", b"<html></html>"),
        ("application/pdf", PNG),
        (DOCX, office("xl")),
        (XLSX, PDF),
    ):
        with pytest.raises(DomainError) as error:
            check_content(declared, data)
        assert error.value.key == "error.file_content_mismatch"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd.pdf", "passwd.pdf"),
        ("C:\\Users\\x\\scan.pdf", "scan.pdf"),
        ("  my   file .pdf ", "my file .pdf"),
        ("evil\u202egnp.exe", "evilgnp.exe"),
        ("nul\x00byte.png", "nulbyte.png"),
        ("...", "upload"),
        (None, "upload"),
        ("а" * 300 + ".pdf", "а" * 146 + ".pdf"),
    ],
)
def test_sanitize_filename(raw, expected):
    assert sanitize_filename(raw) == expected


class FakeFile:
    def __init__(self, data):
        self._buffer = io.BytesIO(data)

    async def read(self, size=-1):
        return self._buffer.read(size)


@pytest.mark.asyncio
async def test_read_limited_stops_above_the_limit():
    assert await read_limited(FakeFile(b"a" * 100), 100) == b"a" * 100
    with pytest.raises(DomainError) as error:
        await read_limited(FakeFile(b"a" * 101), 100)
    assert error.value.key == "error.file_too_large"


class FakeS3:
    def __init__(self):
        self.uploads = []

    def upload_file(self, data, filename, mime_type, folder=""):
        self.uploads.append((filename, mime_type))
        return f"{folder}/{uuid4().hex}"


@pytest.mark.asyncio
async def test_service_rejects_renamed_files_and_stores_a_clean_name(db_session, monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr("app.services.document_service.get_s3_client", lambda: fake)
    from tests.test_services.test_attention_service import vehicle

    v = await vehicle(db_session)
    service = DocumentService(db_session)
    kwargs = dict(entity_type=EntityType.VEHICLE, entity_id=v.id)
    with pytest.raises(DomainError) as error:
        await service.upload(
            file_data=b"MZ\x90\x00 program", filename="invoice.pdf", mime_type="application/pdf", **kwargs
        )
    assert error.value.key == "error.file_content_mismatch" and fake.uploads == []
    doc = await service.upload(file_data=PDF, filename="../../tmp/invoice.pdf", mime_type="application/pdf", **kwargs)
    assert (
        doc.filename == "invoice.pdf"
        and doc.mime_type == "application/pdf"
        and fake.uploads == [("invoice.pdf", "application/pdf")]
    )


def test_office_downloads_are_attachments_and_pdfs_stay_inline(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setattr(settings, "MINIO_EXTERNAL_ENDPOINT", "files.example.com")
    monkeypatch.setattr(settings, "MINIO_ACCESS_KEY", "key")
    monkeypatch.setattr(settings, "MINIO_SECRET_KEY", "secret-secret")
    client = S3Client()
    office_url = client.get_presigned_url("a/b.docx", filename="Протокол.docx", mime_type=DOCX)
    assert "response-content-disposition=attachment" in office_url and "UTF-8" in office_url
    assert "response-content-disposition" not in client.get_presigned_url(
        "a/b.pdf", filename="b.pdf", mime_type="application/pdf"
    )
    assert "response-content-disposition" not in client.get_presigned_url(
        "a/b.png", filename="b.png", mime_type="image/png"
    )
