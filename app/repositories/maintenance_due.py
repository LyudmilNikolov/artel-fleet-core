"""Query for the current planned service per (vehicle, service item).

Shared by the async service and the sync Celery task.
"""

from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import distinct_on

from app.models.maintenance import MaintenanceRecord, MaintenanceStatus, MaintenanceType
from app.models.vehicle import Vehicle, VehicleStatus


def current_service_query(vehicle_id: UUID | None = None, include_decommissioned: bool = False) -> Select:
    """``(record, vehicle)`` of the latest completed planned service per vehicle and service item.

    The latest one supersedes older ones, even when it has no next km / date recorded.
    """
    rec = MaintenanceRecord
    latest = (
        select(rec.id)
        .ext(distinct_on(rec.vehicle_id, rec.service_item))
        .where(
            rec.status == MaintenanceStatus.COMPLETED,
            rec.type == MaintenanceType.SCHEDULED_SERVICE,
            rec.service_item.is_not(None),
        )
        .order_by(
            rec.vehicle_id,
            rec.service_item,
            rec.completed_date.desc().nulls_last(),
            rec.created_at.desc(),
            rec.id.desc(),
        )
    )
    query = select(rec, Vehicle).join(Vehicle, Vehicle.id == rec.vehicle_id).where(rec.id.in_(latest.scalar_subquery()))
    if vehicle_id is not None:
        query = query.where(rec.vehicle_id == vehicle_id)
    if not include_decommissioned:
        query = query.where(Vehicle.status != VehicleStatus.DECOMMISSIONED)
    return query
