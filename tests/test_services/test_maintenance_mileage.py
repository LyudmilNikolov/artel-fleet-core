"""Maintenance mileage: higher values update the vehicle, lower ones need confirmation and add a correction."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.maintenance import MaintenanceRecord
from app.models.mileage import MileageLog
from app.schemas.maintenance import MaintenanceCreate, MaintenanceUpdate
from app.schemas.mileage import MileageCreate
from app.schemas.vehicle import VehicleCreate, VehicleUpdate
from app.services.errors import DomainError, MileageBelowCurrentError
from app.services.maintenance_service import MaintenanceService
from app.services.mileage_service import MileageService
from app.services.vehicle_service import VehicleService
from app.vehicle_rules import MILEAGE_TYPES


async def make_vehicle(db, kind="van", mileage=1000):
    return await VehicleService(db).create(
        VehicleCreate(
            vehicle_type=kind,
            license_plate=None if kind == "forklift" else "CA" + uuid4().hex[:6].upper(),
            brand="MAN",
            model="TGX",
            department="Test department",
            current_mileage=mileage if kind in MILEAGE_TYPES else None,
        )
    )


def payload(vehicle_id, mileage, **kw):
    return MaintenanceCreate(vehicle_id=vehicle_id, type="repair", title="Oil change", mileage_at_service=mileage, **kw)


async def logs(db, vehicle_id):
    return list(
        (await db.scalars(select(MileageLog).where(MileageLog.vehicle_id == vehicle_id).order_by(MileageLog.seq))).all()
    )


@pytest.mark.asyncio
async def test_higher_mileage_updates_vehicle(db_session):
    v = await make_vehicle(db_session)
    record = await MaintenanceService(db_session).create(payload(v.id, 1200), None)
    assert record.mileage_at_service == 1200
    await db_session.refresh(v)
    assert v.current_mileage == 1200
    entries = await logs(db_session, v.id)
    assert [(e.value, e.is_correction, e.notes) for e in entries] == [(1000, False, None), (1200, False, "Oil change")]


@pytest.mark.asyncio
async def test_equal_mileage_adds_no_duplicate_reading(db_session):
    v = await make_vehicle(db_session)
    await MaintenanceService(db_session).create(payload(v.id, 1000), None)
    assert len(await logs(db_session, v.id)) == 1


@pytest.mark.asyncio
async def test_lower_mileage_needs_confirmation_and_writes_nothing(db_session):
    v = await make_vehicle(db_session)
    with pytest.raises(MileageBelowCurrentError) as exc:
        await MaintenanceService(db_session).create(payload(v.id, 900), None)
    assert (exc.value.value, exc.value.previous) == (900, 1000)
    assert exc.value.key == "error.mileage_below_current"
    await db_session.refresh(v)
    assert v.current_mileage == 1000
    assert len(await logs(db_session, v.id)) == 1
    assert (await db_session.scalars(select(MaintenanceRecord))).all() == []


@pytest.mark.asyncio
async def test_confirmed_lower_mileage_adds_correction_and_keeps_history(db_session):
    v = await make_vehicle(db_session)
    service = MaintenanceService(db_session)
    await service.create(payload(v.id, 1200), None)
    record = await service.create(payload(v.id, 900, overwrite_mileage=True), None)
    assert record.mileage_at_service == 900
    await db_session.refresh(v)
    assert v.current_mileage == 900
    entries = await logs(db_session, v.id)
    assert [(e.value, e.is_correction) for e in entries] == [(1000, False), (1200, False), (900, True)]

    mileage = MileageService(db_session)
    # The correction is the new baseline: readings between it and the old maximum are accepted again.
    assert (await mileage.add_reading(MileageCreate(vehicle_id=v.id, value=1100), None)).value == 1100
    with pytest.raises(DomainError) as exc:
        await mileage.add_reading(MileageCreate(vehicle_id=v.id, value=899), None)
    assert exc.value.key == "error.mileage_decreased" and exc.value.params["previous"] == 1100
    await db_session.refresh(v)
    assert v.current_mileage == 1100


@pytest.mark.asyncio
async def test_vehicle_edit_uses_corrected_baseline(db_session):
    v = await make_vehicle(db_session)
    await MaintenanceService(db_session).create(payload(v.id, 1200), None)
    await MaintenanceService(db_session).create(payload(v.id, 900, overwrite_mileage=True), None)
    vehicles = VehicleService(db_session)
    with pytest.raises(DomainError) as exc:
        await vehicles.update(v.id, VehicleUpdate(current_mileage=800))
    assert exc.value.key == "error.mileage_decreased"
    assert (await vehicles.update(v.id, VehicleUpdate(current_mileage=950))).current_mileage == 950
    with pytest.raises(DomainError):
        await vehicles.update(v.id, VehicleUpdate(current_mileage=None))


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["forklift", "container_semi_trailer"])
async def test_untracked_vehicle_rejects_mileage(db_session, kind):
    v = await make_vehicle(db_session, kind)
    with pytest.raises(DomainError) as exc:
        await MaintenanceService(db_session).create(payload(v.id, 500), None)
    assert exc.value.key == "error.mileage_unsupported"
    assert (await db_session.scalars(select(MaintenanceRecord))).all() == []
    assert (
        await MaintenanceService(db_session).create(
            MaintenanceCreate(vehicle_id=v.id, type="repair", title="No km"), None
        )
    ).mileage_at_service is None


@pytest.mark.asyncio
async def test_update_applies_changed_mileage_only(db_session):
    v = await make_vehicle(db_session)
    service = MaintenanceService(db_session)
    record = await service.create(payload(v.id, 1100), None)
    await service.update(record.id, MaintenanceUpdate(title="Renamed", mileage_at_service=1100))
    assert len(await logs(db_session, v.id)) == 2
    await service.update(record.id, MaintenanceUpdate(mileage_at_service=1300))
    await db_session.refresh(v)
    assert v.current_mileage == 1300
    with pytest.raises(MileageBelowCurrentError):
        await service.update(record.id, MaintenanceUpdate(mileage_at_service=1000))
    await db_session.refresh(record)
    assert record.mileage_at_service == 1300
    await service.update(record.id, MaintenanceUpdate(mileage_at_service=1000, overwrite_mileage=True))
    await db_session.refresh(v)
    assert v.current_mileage == 1000 and (await logs(db_session, v.id))[-1].is_correction
    # Clearing the record's mileage does not touch the vehicle.
    await service.update(record.id, MaintenanceUpdate(mileage_at_service=None))
    await db_session.refresh(v)
    assert v.current_mileage == 1000
