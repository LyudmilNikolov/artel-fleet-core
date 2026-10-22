from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select

from app.config import settings
from app.database import get_sync_db
from app.models.compliance import ComplianceDocument
from app.maintenance_rules import service_due
from app.models.driver import Driver, DriverStatus
from app.models.driver_document import DriverDocument
from app.models.maintenance import MaintenanceRecord, MaintenanceStatus
from app.models.vehicle import Vehicle, VehicleStatus
from app.repositories.maintenance_due import current_service_query
from app.repositories.compliance_repo import applicable_clause, current_ids, status_clause
from app.repositories.driver_document_repo import current_ids as driver_current_ids
from app.repositories.driver_document_repo import status_clause as driver_status_clause
from app.tasks.celery_app import celery_app


@celery_app.task
def check_maintenance_reminders():
    """Check for upcoming/overdue maintenance and notify fleet managers."""
    db = get_sync_db()
    try:
        today = date.today()
        upcoming = db.query(MaintenanceRecord).filter(
            MaintenanceRecord.status == MaintenanceStatus.SCHEDULED,
            MaintenanceRecord.scheduled_date <= today + timedelta(days=14),
            MaintenanceRecord.scheduled_date >= today,
        ).all()

        overdue = db.query(MaintenanceRecord).filter(
            MaintenanceRecord.status == MaintenanceStatus.SCHEDULED,
            MaintenanceRecord.scheduled_date < today,
        ).all()

        # TODO: Send notifications for upcoming/overdue maintenance
        return {"upcoming": len(upcoming), "overdue": len(overdue)}
    finally:
        db.close()


@celery_app.task
def check_service_due():
    """Count current planned services (engine oil, gearbox oil, ...) that are overdue or due soon, per item.

    Counting only: sending notifications belongs to the reminders feature. Only the latest completed
    service of each vehicle and item counts; decommissioned vehicles are skipped.
    """
    db = get_sync_db()
    try:
        today = datetime.now(UTC).date()
        by_item: dict[str, dict[str, int]] = {}
        totals = {"overdue": 0, "due_soon": 0}
        for record, vehicle in db.execute(current_service_query()).all():
            due = service_due(
                record.next_service_mileage,
                record.next_service_date,
                vehicle.current_mileage,
                today,
                settings.SERVICE_DUE_SOON_KM,
                settings.SERVICE_DUE_SOON_DAYS,
            )
            if due.status in totals:
                totals[due.status] += 1
                by_item.setdefault(record.service_item, {"overdue": 0, "due_soon": 0})[due.status] += 1
        return {"overdue_service": totals["overdue"], "due_soon_service": totals["due_soon"], "by_item": by_item}
    finally:
        db.close()


@celery_app.task
def check_compliance_expiry():
    """Count current vehicle compliance documents expiring soon or expired (no notifications yet)."""
    db = get_sync_db()
    try:
        today = datetime.now(UTC).date()
        window = settings.COMPLIANCE_EXPIRING_SOON_DAYS
        counts = {}
        for status in ("expiring_soon", "expired"):
            counts[status] = db.execute(
                select(func.count())
                .select_from(ComplianceDocument)
                .join(Vehicle, Vehicle.id == ComplianceDocument.vehicle_id)
                .where(
                    ComplianceDocument.id.in_(current_ids().scalar_subquery()),
                    applicable_clause(),
                    Vehicle.status != VehicleStatus.DECOMMISSIONED,
                    status_clause(status, today, window),
                )
            ).scalar_one()
        return {"expiring_compliance": counts["expiring_soon"], "expired_compliance": counts["expired"]}
    finally:
        db.close()


@celery_app.task
def check_driver_document_expiry():
    """Count current driver documents expiring soon or expired, per type, for active drivers.

    Counting only: sending notifications belongs to the reminders feature. Documents without an
    end date never warn, and history entries (older or renewed ones) are ignored.
    """
    db = get_sync_db()
    try:
        today = datetime.now(UTC).date()
        window = settings.DRIVER_DOCUMENT_EXPIRING_SOON_DAYS
        by_type: dict[str, dict[str, int]] = {}
        totals = {"expiring_soon": 0, "expired": 0}
        for status in totals:
            rows = db.execute(
                select(DriverDocument.document_type, func.count())
                .join(Driver, Driver.id == DriverDocument.driver_id)
                .where(
                    DriverDocument.id.in_(driver_current_ids().scalar_subquery()),
                    Driver.status == DriverStatus.ACTIVE,
                    driver_status_clause(status, today, window),
                )
                .group_by(DriverDocument.document_type)
            ).all()
            for document_type, count in rows:
                by_type.setdefault(document_type, {"expiring_soon": 0, "expired": 0})[status] = count
                totals[status] += count
        return {
            "expiring_driver_documents": totals["expiring_soon"],
            "expired_driver_documents": totals["expired"],
            "by_type": by_type,
        }
    finally:
        db.close()
