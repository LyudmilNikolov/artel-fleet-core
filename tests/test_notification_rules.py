"""The notification source registry covers every tracked document type and has labels in both catalogs."""

import json
from pathlib import Path

from app.driver_document_rules import DriverDocumentType
from app.notification_rules import SOURCE_BY_CODE, SOURCES, source_code, source_label_key
from app.vehicle_rules import ComplianceType

I18N = Path(__file__).resolve().parents[1] / "app" / "i18n"


def test_every_document_type_is_a_source():
    codes = {s.code for s in SOURCES}
    assert codes == {f"vehicle:{t.value}" for t in ComplianceType} | {f"driver:{t.value}" for t in DriverDocumentType}
    assert len(codes) == len(SOURCES) == 12


def test_every_source_has_a_label_in_bg_and_en():
    for lang in ("bg", "en"):
        catalog = json.loads((I18N / lang / "messages.json").read_text(encoding="utf-8"))
        for source in SOURCES:
            assert catalog.get(source.label_key), f"{lang}: missing {source.label_key}"


def test_source_helpers():
    assert source_code("compliance_document", "casco") == "vehicle:casco"
    assert source_code("driver_document", "id_card") == "driver:id_card"
    assert source_label_key("driver:adr_certificate") == "notification_source.driver:adr_certificate"
    assert SOURCE_BY_CODE["vehicle:tachograph"].entity_type == "compliance_document"
