"""Uploaded files are checked by their content, not by what the browser claims.

The declared MIME type must be allowed AND the first bytes (and, for Office files, the archive layout) must match it.
A renamed program, an HTML page called ``x.pdf`` or a script labelled as an image is rejected.
"""

import io
import re
import unicodedata
import zipfile

from app.services.errors import DomainError

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
OLE_TYPES = {"application/msword", "application/vnd.ms-excel"}
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# Shown inline by the browser; everything else is downloaded as an attachment
INLINE_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/gif", "image/webp"}


def detect_mime(data: bytes) -> str | None:
    """The type the content really is, or None when it is not one of the supported formats.

    Old Word and Excel files share one container format, so they are reported as ``msword``; the caller accepts
    either declared OLE type.
    """
    head = data[:16]
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(OLE_MAGIC):
        return "application/msword"
    if head.startswith(b"PK\x03\x04"):
        try:
            names = zipfile.ZipFile(io.BytesIO(data)).namelist()  # only the file list is read, nothing is extracted
        except (zipfile.BadZipFile, ValueError, OSError):
            return None
        if any(n.startswith("xl/") for n in names):
            return XLSX
        if any(n.startswith("word/") for n in names):
            return DOCX
    return None


def check_content(declared: str, data: bytes) -> str:
    """Return the MIME type to store, or raise ``error.file_content_mismatch`` when content and claim disagree."""
    detected = detect_mime(data)
    if detected is None:
        raise DomainError("error.file_content_mismatch")
    if declared in OLE_TYPES and detected == "application/msword":
        return declared
    if detected != declared:
        raise DomainError("error.file_content_mismatch")
    return detected


def sanitize_filename(name: str | None, default: str = "upload") -> str:
    """A safe display name: no path parts, control or bidi characters, at most 150 characters."""
    name = (name or "").replace("\\", "/").split("/")[-1]
    name = unicodedata.normalize("NFC", name)
    name = "".join(ch for ch in name if unicodedata.category(ch) not in ("Cc", "Cf"))
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    if len(name) > 150:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: 150 - len(ext) - 1] + "." + ext) if dot and len(ext) <= 10 else name[:150]
    return name or default


async def read_limited(file, limit: int) -> bytes:
    """Read an UploadFile but stop early when it is bigger than ``limit`` bytes."""
    chunks, total = [], 0
    while chunk := await file.read(1024 * 1024):
        total += len(chunk)
        if total > limit:
            raise DomainError("error.file_too_large")
        chunks.append(chunk)
    return b"".join(chunks)
