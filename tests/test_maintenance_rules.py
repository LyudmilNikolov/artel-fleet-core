"""Service items and the computed due state of a planned service."""

import json
from datetime import date
from pathlib import Path

import pytest

from app.maintenance_rules import (
    SERVICE_CHECKLIST,
    SERVICE_CHECKS,
    SERVICE_ITEMS,
    ServiceItem,
    normalize_checklist,
    service_due,
    service_items_for,
    validate_service_selection,
)

TODAY = date(2026, 10, 21)


def due(next_km=None, next_date=None, current_km=None):
    return service_due(next_km, next_date, current_km, TODAY, 500, 30)


def test_items_have_labels_in_both_catalogs():
    assert list(SERVICE_ITEMS) == [i.value for i in ServiceItem]
    assert {"engine_oil", "gearbox_oil", "differential_oil"} <= set(SERVICE_ITEMS)
    for lang in ("bg", "en"):
        catalog = json.loads(Path(f"app/i18n/{lang}/messages.json").read_text())
        for item in SERVICE_ITEMS:
            assert f"service_item.{item}" in catalog, (lang, item)


@pytest.mark.parametrize(
    ("next_km", "next_date", "current_km", "status", "km_left", "days_left"),
    [
        (None, None, 100, "none", None, None),
        (160000, None, 100000, "ok", 60000, None),
        (160000, None, 159500, "due_soon", 500, None),  # inside the 500 km window (inclusive)
        (160000, None, 159501, "due_soon", 499, None),
        (160000, None, 159499, "ok", 501, None),  # just outside the window
        (160000, None, 158999, "ok", 1001, None),
        (160000, None, 160000, "overdue", 0, None),  # reached the due km
        (160000, None, 161000, "overdue", -1000, None),
        (None, date(2027, 3, 1), 100, "ok", None, 131),
        (None, date(2026, 11, 20), None, "due_soon", None, 30),  # inside the 30 day window (inclusive)
        (None, date(2026, 10, 21), None, "due_soon", None, 0),  # due today is the last day
        (None, date(2026, 10, 20), None, "overdue", None, -1),
        (160000, date(2026, 10, 20), 100000, "overdue", 60000, -1),  # the date alone is enough
        (160000, date(2027, 6, 1), 159800, "due_soon", 200, 223),  # the km alone is enough
    ],
)
def test_due_state(next_km, next_date, current_km, status, km_left, days_left):
    state = due(next_km, next_date, current_km)
    assert (state.status, state.km_left, state.days_left) == (status, km_left, days_left)


def test_km_is_ignored_without_a_current_mileage():
    """Trailers and forklifts have no odometer: only the date is evaluated."""
    state = due(160000, date(2027, 6, 1), None)
    assert (state.status, state.km_left) == ("ok", None)
    assert due(160000, None, None).status == "none"


def test_filters_are_checklist_options_not_service_items():
    assert not {"oil_filter", "air_filter", "fuel_filter"} & set(SERVICE_ITEMS)
    assert SERVICE_CHECKLIST["engine_oil"] == ("oil_filter", "air_filter", "fuel_filter") == SERVICE_CHECKS
    assert "gearbox_oil" not in SERVICE_CHECKLIST
    for lang in ("bg", "en"):
        catalog = json.loads(Path(f"app/i18n/{lang}/messages.json").read_text())
        for check in SERVICE_CHECKS:
            assert f"service_check.{check}" in catalog, (lang, check)
            assert f"service_item.{check}" not in catalog


def test_trailers_have_no_service_items():
    assert service_items_for("container_semi_trailer") == () == service_items_for("curtain_side_semi_trailer")
    for kind in ("tractor_unit", "van", "passenger_car", "forklift"):
        assert service_items_for(kind) == SERVICE_ITEMS
    assert service_items_for(None) == SERVICE_ITEMS  # no vehicle chosen yet


def test_selection_validation():
    validate_service_selection("van", "engine_oil", ["oil_filter", "air_filter"])
    validate_service_selection("curtain_side_semi_trailer", None, None)
    with pytest.raises(ValueError, match="not_for_vehicle"):
        validate_service_selection("curtain_side_semi_trailer", "engine_oil", None)
    with pytest.raises(ValueError, match="checklist_not_allowed"):
        validate_service_selection("van", "gearbox_oil", ["oil_filter"])
    with pytest.raises(ValueError, match="checklist_not_allowed"):
        validate_service_selection("van", None, ["oil_filter"])


def test_normalize_checklist():
    assert normalize_checklist(["fuel_filter", "oil_filter", "oil_filter"]) == ["oil_filter", "fuel_filter"]
    assert normalize_checklist([]) is None and normalize_checklist(None) is None
