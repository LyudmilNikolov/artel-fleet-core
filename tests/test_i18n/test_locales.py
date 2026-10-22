import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from app.i18n import _, get_available_languages
from app.i18n.locales import header_language, resolve_locale


def request(path="/", query="", session=None, header=""):
    return Request(
        {
            "type": "http",
            "path": path,
            "scheme": "http",
            "server": ("test", 80),
            "query_string": query.encode(),
            "headers": [(b"accept-language", header.encode())],
            "session": session or {},
        }
    )


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("en-GB;q=.7,bg-BG;q=.9", "bg"),
        ("en;q=0,bg;q=.5", "bg"),
        ("ru,*;q=.9", None),
        ("en-US", "en"),
        ("bg;q=invalid,en;q=.5", "en"),
    ],
)
def test_header_preferences(header, expected):
    assert header_language(header) == expected


def test_browser_ignores_header_and_keeps_requests_isolated():
    bg = request(header="en-GB")
    en = request(session={"lang": "en"})
    assert resolve_locale(bg) == "bg"
    assert resolve_locale(en) == "en"
    assert resolve_locale(bg) == "bg"
    assert _("nav.drivers", bg.state.locale) == "Водачи"
    assert _("nav.drivers", en.state.locale) == "Drivers"


def test_invalid_session_does_not_hide_saved_english():
    r = request(session={"lang": "ru"})
    assert resolve_locale(r, SimpleNamespace(language="en")) == "en"
    assert r.session["lang"] == "en"


def test_api_priority_and_query_is_request_local():
    r = request("/api/v1/reports", "lang=bg", {"lang": "en"}, "en-GB")
    assert resolve_locale(r, SimpleNamespace(language="en")) == "bg"
    assert r.session["lang"] == "en"
    assert resolve_locale(request("/api/v1/reports", header="en-US")) == "en"


def test_catalog_integrity():
    root = Path("app/i18n")

    def load(language):
        def unique(pairs):
            result = {}
            for key, value in pairs:
                assert key not in result
                result[key] = value
            return result

        return json.loads((root / language / "messages.json").read_text(), object_pairs_hook=unique)

    en, bg = load("en"), load("bg")
    assert en.keys() == bg.keys()
    assert len(en) >= 427
    for key in en:
        assert set(re.findall(r"\{\w+\}", en[key])) == set(re.findall(r"\{\w+\}", bg[key])), key
    assert {x["code"] for x in get_available_languages()} == {"bg", "en"}
    assert _("nav.drivers", "ru") == "Водачи"


def test_template_translation_keys_exist():
    keys = json.loads(Path("app/i18n/en/messages.json").read_text())
    for path in Path("app/templates").rglob("*.html"):
        for key in re.findall(r"""_\(['"]([^'"]+)['"]\s*[,)]""", path.read_text()):
            assert key in keys, (str(path), key)


def test_dynamic_enum_translation_keys():
    from app.driver_document_rules import DriverDocumentType
    from app.models.audit_log import AuditAction
    from app.models.document import DocumentType, EntityType
    from app.models.driver import DriverStatus
    from app.models.expense import ExpenseCategory
    from app.models.maintenance import MaintenanceStatus, MaintenanceType
    from app.models.user import UserRole
    from app.models.vehicle import FuelType, TransmissionType, VehicleStatus, VehicleType
    from app.repositories.compliance_repo import STATUSES as COMPLIANCE_STATUSES
    from app.vehicle_rules import ComplianceType

    groups = {
        "audit_action": AuditAction,
        "compliance_type": ComplianceType,
        "driver_document_type": DriverDocumentType,
        "doc_type": DocumentType,
        "expense_category": ExpenseCategory,
        "maintenance_type": MaintenanceType,
        "role": UserRole,
        "vehicle_type": VehicleType,
        "fuel_type": FuelType,
        "transmission": TransmissionType,
    }
    keys = json.loads(Path("app/i18n/en/messages.json").read_text())
    for entity in [*(choice.value for choice in EntityType), "user", "session"]:
        assert f"audit_entity.{entity}" in keys
    for prefix, choices in groups.items():
        for choice in choices:
            assert f"{prefix}.{choice.value}" in keys
    for status in (*COMPLIANCE_STATUSES, "replaced", "missing", "no_expiry"):
        assert f"compliance_status.{status}" in keys
    for choices in (DriverStatus, MaintenanceStatus, VehicleStatus):
        for choice in choices:
            assert f"status.{choice.value}" in keys


@pytest.mark.parametrize("language", ["ru", "kz", "tr", "BG", "EN", "bg-BG", None])
def test_preference_writes_reject_unsupported_values(language):
    from pydantic import ValidationError

    from app.schemas.user import UserUpdate

    with pytest.raises(ValidationError):
        UserUpdate(language=language)


def test_preference_update_may_be_omitted():
    from app.schemas.user import UserUpdate

    assert UserUpdate().model_dump(exclude_unset=True) == {}


def test_invalid_anonymous_session_is_removed_before_saved_preference_resolution():
    r = request(session={"lang": "ru"})
    assert resolve_locale(r) == "bg"
    assert "lang" not in r.session
    assert resolve_locale(r, SimpleNamespace(language="en")) == "en"
