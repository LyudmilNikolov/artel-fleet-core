"""Compliance document applicability and computed status (no database)."""

import json
from datetime import date
from pathlib import Path

import pytest

from app.services.compliance_service import compliance_status, renewal_dates
from app.vehicle_rules import (
    COMPLIANCE_TYPES,
    ComplianceType,
    VehicleType,
    compliance_types_for,
    validate_compliance_type,
)

ROAD = {"civil_liability", "casco", "technical_inspection"}


def test_matrix_covers_every_vehicle_type():
    assert set(COMPLIANCE_TYPES) == {t.value for t in VehicleType}
    assert set(compliance_types_for("tractor_unit")) == ROAD | {"tachograph"}
    for kind in ("container_semi_trailer", "curtain_side_semi_trailer", "van", "passenger_car"):
        assert set(compliance_types_for(kind)) == ROAD
    assert compliance_types_for(VehicleType.FORKLIFT) == ()
    assert set().union(*COMPLIANCE_TYPES.values()) == {t.value for t in ComplianceType}


@pytest.mark.parametrize(
    "vehicle_type,document_type,error",
    [
        ("van", "tachograph", "error.compliance_type_not_applicable"),
        ("container_semi_trailer", "tachograph", "error.compliance_type_not_applicable"),
        ("forklift", "civil_liability", "error.compliance_type_not_applicable"),
        ("tractor_unit", "leasing", "error.compliance_type_unknown"),
    ],
)
def test_incompatible_pairs_rejected(vehicle_type, document_type, error):
    with pytest.raises(ValueError, match=error):
        validate_compliance_type(vehicle_type, document_type)


def test_compatible_pair_accepted():
    validate_compliance_type(VehicleType.TRACTOR_UNIT, ComplianceType.TACHOGRAPH)


@pytest.mark.parametrize("lang", ["bg", "en"])
def test_every_type_and_status_has_labels(lang):
    keys = json.loads(Path(f"app/i18n/{lang}/messages.json").read_text())
    for t in ComplianceType:
        assert keys[f"compliance_type.{t.value}"]
    for s in ("valid", "expiring_soon", "expired", "replaced", "missing"):
        assert keys[f"compliance_status.{s}"]


def test_status_boundaries():
    today = date(2026, 10, 21)
    assert compliance_status(date(2026, 10, 20), today, 30) == "expired"
    assert compliance_status(today, today, 30) == "expiring_soon"
    assert compliance_status(date(2026, 11, 20), today, 30) == "expiring_soon"
    assert compliance_status(date(2026, 11, 21), today, 30) == "valid"


@pytest.mark.parametrize(
    "start,end,expected",
    [
        (date(2025, 1, 1), date(2025, 12, 31), (date(2026, 1, 1), date(2026, 12, 31))),
        (date(2027, 3, 1), date(2028, 2, 29), (date(2028, 3, 1), date(2029, 2, 28))),
        (date(2025, 6, 15), date(2027, 6, 14), (date(2027, 6, 15), date(2029, 6, 14))),
        (date(2026, 1, 1), date(2026, 1, 10), (date(2026, 1, 11), date(2026, 1, 20))),
    ],
)
def test_renewal_proposal_keeps_period(start, end, expected):
    assert renewal_dates(start, end) == expected
