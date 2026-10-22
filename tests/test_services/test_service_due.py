"""Planned service items: validation, the current service per item, computed due state, attention counts."""

from datetime import date, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.maintenance import MaintenanceType
from app.models.vehicle import VehicleStatus
from app.schemas.maintenance import MaintenanceCreate, MaintenanceUpdate
from app.schemas.vehicle import VehicleCreate
from app.services.dashboard_service import DashboardService
from app.services.errors import DomainError
from app.services.maintenance_service import MaintenanceService
from app.services.service_due_service import ServiceDueService
from app.services.vehicle_service import VehicleService

TODAY = date(2026, 10, 21)


async def make_vehicle(db, kind="van", mileage=100000, plate=None):
    return await VehicleService(db).create(
        VehicleCreate(
            vehicle_type=kind,
            license_plate=plate or "CA" + uuid4().hex[:6].upper(),
            brand="Volvo",
            model="FH",
            department="Test department",
            current_mileage=mileage if kind in ("van", "tractor_unit", "passenger_car") else None,
        )
    )


async def service_record(db, vehicle, item="engine_oil", done=TODAY - timedelta(days=100), km=100000, **kw):
    """A completed planned service."""
    if km is not None:
        kw.setdefault("next_service_mileage", km + 60000)
    return await MaintenanceService(db).create(
        MaintenanceCreate(
            vehicle_id=vehicle.id,
            type="scheduled_service",
            title="Смяна на масло",
            service_item=item,
            status="completed",
            completed_date=done,
            mileage_at_service=km,
            **kw,
        ),
        None,
    )


def due_service(db):
    return ServiceDueService(db, today=TODAY, km_window=500, day_window=30)


@pytest.mark.asyncio
async def test_each_item_is_tracked_separately(db_session):
    v = await make_vehicle(db_session)
    await service_record(db_session, v, "engine_oil", next_service_mileage=160000)
    await service_record(db_session, v, "gearbox_oil", km=100000, next_service_mileage=250000)
    await service_record(db_session, v, "differential_oil", next_service_date=TODAY + timedelta(days=10), next_service_mileage=None)
    rows = {r.record.service_item: r for r in await due_service(db_session).for_vehicle(v.id)}
    assert list(rows) == ["engine_oil", "gearbox_oil", "differential_oil"]  # display order
    assert rows["engine_oil"].status == "ok" and rows["engine_oil"].due.km_left == 60000
    assert rows["gearbox_oil"].due.km_left == 150000
    assert rows["differential_oil"].status == "due_soon" and rows["differential_oil"].due.days_left == 10


@pytest.mark.asyncio
async def test_newer_service_supersedes_older_and_old_ones_never_warn(db_session):
    v = await make_vehicle(db_session, mileage=200000)
    # km=None: no service odometer reading recorded, so the lower value cannot clash with the vehicle's mileage
    old = await service_record(db_session, v, done=TODAY - timedelta(days=400), km=None, next_service_mileage=150000)
    svc = due_service(db_session)
    assert [r.status for r in await svc.list_due()] == ["overdue"]  # 200,000 km >= 150,000 km
    new = await service_record(db_session, v, done=TODAY - timedelta(days=5), km=200000, next_service_mileage=260000)
    rows = await svc.for_vehicle(v.id)
    assert [r.record.id for r in rows] == [new.id] and rows[0].status == "ok"
    assert await svc.attention_counts() == {"overdue": 0, "due_soon": 0}
    assert await svc.state_for_record(old) is None  # history: no state
    assert (await svc.state_for_record(new)).status == "ok"


@pytest.mark.asyncio
async def test_a_newer_service_without_next_due_replaces_the_old_due(db_session):
    v = await make_vehicle(db_session, mileage=200000)
    await service_record(db_session, v, done=TODAY - timedelta(days=400), km=None, next_service_mileage=150000)
    newest = await service_record(
        db_session, v, done=TODAY - timedelta(days=1), km=200000, next_service_mileage=None, next_service_date=None
    )
    rows = await due_service(db_session).for_vehicle(v.id)
    assert [(r.record.id, r.status) for r in rows] == [(newest.id, "none")]
    assert await due_service(db_session).attention_counts() == {"overdue": 0, "due_soon": 0}


@pytest.mark.asyncio
async def test_only_completed_planned_services_with_an_item_count(db_session):
    v = await make_vehicle(db_session, mileage=500000)
    svc = MaintenanceService(db_session)
    common = dict(vehicle_id=v.id, title="x", next_service_mileage=None)
    await svc.create(MaintenanceCreate(type="scheduled_service", service_item="engine_oil", status="scheduled", **common), None)
    await svc.create(MaintenanceCreate(type="scheduled_service", status="completed", next_service_date=TODAY - timedelta(days=9), **common), None)
    await svc.create(MaintenanceCreate(type="repair", status="completed", next_service_date=TODAY - timedelta(days=9), **common), None)
    assert await due_service(db_session).list_due() == []


@pytest.mark.asyncio
async def test_decommissioned_vehicles_never_warn(db_session):
    v = await make_vehicle(db_session)
    await service_record(db_session, v, next_service_date=TODAY - timedelta(days=3))
    assert await due_service(db_session).attention_counts() == {"overdue": 1, "due_soon": 0}
    v.status = VehicleStatus.DECOMMISSIONED
    await db_session.flush()
    assert await due_service(db_session).attention_counts() == {"overdue": 0, "due_soon": 0}
    assert len(await due_service(db_session).for_vehicle(v.id)) == 1  # still visible on the vehicle itself


@pytest.mark.asyncio
async def test_vehicle_without_odometer_uses_the_date_only(db_session):
    forklift = await VehicleService(db_session).create(
        VehicleCreate(vehicle_type="forklift", brand="Linde", model="H25", department="Test department")
    )
    await service_record(
        db_session, forklift, "gearbox_oil", km=None, next_service_mileage=1000, next_service_date=TODAY + timedelta(days=200)
    )
    rows = await due_service(db_session).list_due()
    assert rows[0].status == "ok" and rows[0].due.km_left is None


@pytest.mark.asyncio
async def test_trailers_cannot_have_a_service_item(db_session):
    trailer = await make_vehicle(db_session, "curtain_side_semi_trailer", plate="CA9999TT")
    svc = MaintenanceService(db_session)
    with pytest.raises(DomainError, match="service_item_not_for_vehicle"):
        await svc.create(
            MaintenanceCreate(vehicle_id=trailer.id, type="scheduled_service", title="x", service_item="engine_oil"), None
        )
    plain = await svc.create(MaintenanceCreate(vehicle_id=trailer.id, type="scheduled_service", title="Годишен преглед"), None)
    assert plain.service_item is None
    with pytest.raises(DomainError, match="service_item_not_for_vehicle"):
        await svc.update(plain.id, MaintenanceUpdate(service_item="gearbox_oil"))
    # unrelated edits of a record never trip over the vehicle type
    assert (await svc.update(plain.id, MaintenanceUpdate(title="Друго"))).title == "Друго"


@pytest.mark.asyncio
async def test_engine_oil_checklist(db_session):
    v = await make_vehicle(db_session)
    svc = MaintenanceService(db_session)
    record = await service_record(db_session, v, "engine_oil", service_checklist=["fuel_filter", "oil_filter", "oil_filter"])
    assert record.service_checklist == ["oil_filter", "fuel_filter"]  # unique, display order, plain strings
    assert all(type(c) is str for c in record.service_checklist)
    with pytest.raises(DomainError, match="checklist_not_allowed"):
        await service_record(db_session, v, "gearbox_oil", service_checklist=["oil_filter"], km=None)
    with pytest.raises(DomainError, match="service_item_not_allowed"):
        await svc.create(
            MaintenanceCreate(vehicle_id=v.id, type="repair", title="x", service_checklist=["air_filter"]), None
        )
    with pytest.raises(ValidationError):
        MaintenanceCreate(vehicle_id=v.id, type="scheduled_service", title="x", service_checklist=["windshield"])
    # the filters are not tracked as items of their own
    assert [r.record.service_item for r in await due_service(db_session).for_vehicle(v.id)] == ["engine_oil"]

    changed = await svc.update(record.id, MaintenanceUpdate(service_checklist=["air_filter"]))
    assert changed.service_checklist == ["air_filter"]
    cleared = await svc.update(record.id, MaintenanceUpdate(service_checklist=None))
    assert cleared.service_checklist is None


@pytest.mark.asyncio
async def test_changing_item_or_type_drops_the_checklist(db_session):
    v = await make_vehicle(db_session)
    svc = MaintenanceService(db_session)
    record = await service_record(db_session, v, "engine_oil", service_checklist=["oil_filter"])
    with pytest.raises(DomainError, match="checklist_not_allowed"):
        await svc.update(record.id, MaintenanceUpdate(service_item="gearbox_oil", service_checklist=["oil_filter"]))
    moved = await svc.update(record.id, MaintenanceUpdate(service_item="gearbox_oil"))
    assert moved.service_item == "gearbox_oil" and moved.service_checklist is None
    other = await service_record(db_session, v, "engine_oil", service_checklist=["air_filter"], km=None, done=TODAY)
    kept = await svc.update(other.id, MaintenanceUpdate(title="Нов заглавие"))
    assert kept.service_checklist == ["air_filter"]
    repaired = await svc.update(other.id, MaintenanceUpdate(type="repair"))
    assert repaired.service_item is None and repaired.service_checklist is None
    with pytest.raises(DomainError, match="service_item_not_allowed"):
        await svc.update(other.id, MaintenanceUpdate(service_checklist=["air_filter"]))


@pytest.mark.asyncio
async def test_list_due_is_ordered_by_urgency_and_filterable(db_session):
    a = await make_vehicle(db_session, plate="CA0001AA")
    b = await make_vehicle(db_session, plate="CA0002BB")
    await service_record(db_session, a, "engine_oil", next_service_mileage=None, next_service_date=TODAY + timedelta(days=100))
    await service_record(db_session, b, "engine_oil", next_service_mileage=None, next_service_date=TODAY - timedelta(days=2))
    await service_record(db_session, b, "coolant", next_service_mileage=None, next_service_date=TODAY + timedelta(days=5))
    svc = due_service(db_session)
    assert [r.status for r in await svc.list_due()] == ["overdue", "due_soon", "ok"]
    assert [r.record.service_item for r in await svc.list_due("due_soon")] == ["coolant"]
    assert await svc.attention_counts() == {"overdue": 1, "due_soon": 1}
    with pytest.raises(DomainError):
        await svc.list_due("bogus")
    stats = await DashboardService(db_session).attention_needed()
    assert "overdue_service" in stats and "due_soon_service" in stats


@pytest.mark.asyncio
async def test_validation_rules(db_session):
    v = await make_vehicle(db_session)
    svc = MaintenanceService(db_session)
    with pytest.raises(DomainError, match="service_item_not_allowed"):
        await svc.create(MaintenanceCreate(vehicle_id=v.id, type="repair", title="x", service_item="engine_oil"), None)
    with pytest.raises(DomainError, match="next_km_not_after"):
        await svc.create(
            MaintenanceCreate(vehicle_id=v.id, type="scheduled_service", title="x", mileage_at_service=100000, next_service_mileage=100000),
            None,
        )
    with pytest.raises(ValidationError):
        MaintenanceCreate(vehicle_id=v.id, type="scheduled_service", title="x", service_item="windshield")
    with pytest.raises(ValidationError):
        MaintenanceCreate(vehicle_id=v.id, type="scheduled_service", title="x", next_service_mileage=-5)


@pytest.mark.asyncio
async def test_changing_the_type_clears_the_item_but_keeps_the_due_values(db_session):
    v = await make_vehicle(db_session)
    record = await service_record(db_session, v, next_service_date=TODAY + timedelta(days=90))
    svc = MaintenanceService(db_session)
    with pytest.raises(DomainError, match="service_item_not_allowed"):
        await svc.update(record.id, MaintenanceUpdate(type="repair", service_item="engine_oil"))
    changed = await svc.update(record.id, MaintenanceUpdate(type="repair"))
    assert changed.type == MaintenanceType.REPAIR and changed.service_item is None
    assert changed.next_service_mileage == 160000 and changed.next_service_date == TODAY + timedelta(days=90)
    assert await due_service(db_session).list_due() == []  # no longer a planned service


@pytest.mark.asyncio
async def test_update_item_and_due_values(db_session):
    v = await make_vehicle(db_session)
    record = await service_record(db_session, v, "engine_oil")
    svc = MaintenanceService(db_session)
    changed = await svc.update(record.id, MaintenanceUpdate(service_item="gearbox_oil", next_service_mileage=190000))
    assert changed.service_item == "gearbox_oil" and changed.next_service_mileage == 190000
    with pytest.raises(DomainError, match="next_km_not_after"):
        await svc.update(record.id, MaintenanceUpdate(next_service_mileage=90000))
    cleared = await svc.update(record.id, MaintenanceUpdate(next_service_mileage=None, next_service_date=None))
    assert cleared.next_service_mileage is None and cleared.next_service_date is None


@pytest.mark.asyncio
async def test_default_warning_window_is_ten_thousand_km(db_session):
    """An oil change is flagged once the next service is within 10,000 km."""
    from app.config import settings

    assert settings.SERVICE_DUE_SOON_KM == 10000
    v = await make_vehicle(db_session, mileage=100000)
    await service_record(db_session, v, "engine_oil", next_service_mileage=109000)
    await service_record(db_session, v, "gearbox_oil", next_service_mileage=110000)
    await service_record(db_session, v, "differential_oil", next_service_mileage=110001)
    svc = ServiceDueService(db_session, today=TODAY)  # default windows from the settings
    states = {r.record.service_item: (r.status, r.due.km_left) for r in await svc.for_vehicle(v.id)}
    assert states == {
        "engine_oil": ("due_soon", 9000),
        "gearbox_oil": ("due_soon", 10000),  # exactly 10,000 km left is inside the window
        "differential_oil": ("ok", 10001),
    }
    assert (await svc.attention_counts())["due_soon"] == 2
