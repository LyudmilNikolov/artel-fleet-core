"""Central driver document definition: labels, per-type rules, normalisation, masking."""

import json
from pathlib import Path

import pytest

from app.driver_document_rules import (
    DRIVER_DOCUMENT_TYPES,
    DriverDocumentType,
    mask_number,
    normalize_categories,
    validate_driver_document,
)


def test_enum_and_spec_table_agree():
    assert list(DRIVER_DOCUMENT_TYPES) == [t.value for t in DriverDocumentType]


def test_every_type_has_both_labels_and_a_valid_scan_type():
    from app.models.document import DocumentType

    scan_types = {t.value for t in DocumentType}
    for lang in ("bg", "en"):
        catalog = json.loads(Path(f"app/i18n/{lang}/messages.json").read_text())
        for code, spec in DRIVER_DOCUMENT_TYPES.items():
            assert f"driver_document_type.{code}" in catalog, (lang, code)
            assert spec.upload_doc_type in scan_types
    # the requested starting types
    assert {"id_card", "driving_licence", "tachograph_card", "psycho_test", "professional_competence"} <= set(
        DRIVER_DOCUMENT_TYPES
    )


def test_only_the_driving_licence_is_tagged_as_a_driving_licence_scan():
    """The `license` scan type is labelled "driving licence"; ADR and other certificates must not use it."""
    tagged = [c for c, spec in DRIVER_DOCUMENT_TYPES.items() if spec.upload_doc_type == "license"]
    assert tagged == ["driving_licence"]


def test_number_required_flags():
    assert DRIVER_DOCUMENT_TYPES["id_card"].number_required
    assert DRIVER_DOCUMENT_TYPES["driving_licence"].number_required
    assert DRIVER_DOCUMENT_TYPES["tachograph_card"].number_required
    assert not DRIVER_DOCUMENT_TYPES["psycho_test"].number_required
    assert not DRIVER_DOCUMENT_TYPES["professional_competence"].number_required


@pytest.mark.parametrize(
    ("document_type", "number", "categories", "error"),
    [
        ("driving_licence", None, None, "error.driver_document_number_required"),
        ("id_card", "123456789", "B", "error.driver_document_categories_not_allowed"),
        ("passport", "1", None, "error.driver_document_type_unknown"),
        ("driving_licence", "1", "B, D", "error.driver_document_category_invalid"),
    ],
)
def test_validation_errors(document_type, number, categories, error):
    with pytest.raises(ValueError, match=error):
        validate_driver_document(document_type, number, categories)


def test_licence_category_options():
    from app.driver_document_rules import DRIVING_LICENCE_CATEGORIES

    assert DRIVING_LICENCE_CATEGORIES == ("B", "C", "CE")


def test_valid_combinations():
    validate_driver_document("driving_licence", "123", "B, C")
    validate_driver_document("psycho_test", None, None)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("b, c;ce", "B, C, CE"),
        (" B ,, c ", "B, C"),
        ("", None),
        ("  ", None),
        (None, None),
        ("ce, b, c, b", "B, C, CE"),
    ],
)
def test_normalize_categories(raw, expected):
    assert normalize_categories(raw) == expected


def test_mask_number():
    assert mask_number("1234567890") == "•••• 7890"
    assert mask_number("123") == "••••"
    assert mask_number(None) is None
    assert mask_number("") == ""
