"""Authoritative definition of tracked driver documents (Лицензи и документи).

Types are stored as plain strings, so adding a type means adding one entry to
``DRIVER_DOCUMENT_TYPES`` (and ``DriverDocumentType``) plus ``driver_document_type.<code>``
labels in both catalogs. No migration is needed.
"""

from dataclasses import dataclass
from enum import StrEnum


class DriverDocumentType(StrEnum):
    """Document types a driver can hold, with a validity period."""

    ID_CARD = "id_card"  # Лична карта
    DRIVING_LICENCE = "driving_licence"  # Книжка
    TACHOGRAPH_CARD = "tachograph_card"  # Дигитал (карта на водача)
    PSYCHO_TEST = "psycho_test"  # Психо
    PROFESSIONAL_COMPETENCE = "professional_competence"  # Компетентност (КПК / CPC)
    MEDICAL_CERTIFICATE = "medical_certificate"  # Медицинско удостоверение


@dataclass(frozen=True)
class DriverDocumentSpec:
    """Per-type rules: whether a number is mandatory, whether categories apply, scan type."""

    number_required: bool
    has_categories: bool
    upload_doc_type: str  # app.models.document.DocumentType value used for scans


# Display order. Keep in sync with DriverDocumentType.
DRIVER_DOCUMENT_TYPES: dict[str, DriverDocumentSpec] = {
    "id_card": DriverDocumentSpec(number_required=True, has_categories=False, upload_doc_type="scan"),
    "driving_licence": DriverDocumentSpec(number_required=True, has_categories=True, upload_doc_type="license"),
    "tachograph_card": DriverDocumentSpec(number_required=True, has_categories=False, upload_doc_type="scan"),
    "psycho_test": DriverDocumentSpec(number_required=False, has_categories=False, upload_doc_type="medical"),
    "professional_competence": DriverDocumentSpec(number_required=False, has_categories=False, upload_doc_type="scan"),
    "medical_certificate": DriverDocumentSpec(number_required=False, has_categories=False, upload_doc_type="medical"),
}

CATEGORIES_MAX_LENGTH = 50
# Driving licence categories offered in the form, in display order.
DRIVING_LICENCE_CATEGORIES: tuple[str, ...] = ("B", "C", "CE")


def driver_document_types() -> tuple[str, ...]:
    """Type codes in display order."""
    return tuple(DRIVER_DOCUMENT_TYPES)


def validate_driver_document(document_type: str, number: str | None, categories: str | None) -> None:
    """Raise ValueError (an error key) when type, number or categories break the per-type rules."""
    spec = DRIVER_DOCUMENT_TYPES.get(document_type)
    if spec is None:
        raise ValueError("error.driver_document_type_unknown")
    if spec.number_required and not number:
        raise ValueError("error.driver_document_number_required")
    if categories and not spec.has_categories:
        raise ValueError("error.driver_document_categories_not_allowed")
    if categories and any(c not in DRIVING_LICENCE_CATEGORIES for c in categories.split(", ")):
        raise ValueError("error.driver_document_category_invalid")


def normalize_categories(value: str | None) -> str | None:
    """Trim, uppercase, drop duplicates and join as ``B, C, CE`` (known categories in display order)."""
    if value is None:
        return None
    parts = [p.strip().upper() for p in value.replace(";", ",").replace("/", ",").split(",")]
    parts = list(dict.fromkeys(p for p in parts if p))
    known = [c for c in DRIVING_LICENCE_CATEGORIES if c in parts]
    return ", ".join(known + [p for p in parts if p not in known]) or None


def mask_number(value: str | None) -> str | None:
    """Hide all but the last four characters of a document number (``•••• 1234``)."""
    if not value:
        return value
    return "•••• " + value[-4:] if len(value) > 4 else "••••"
