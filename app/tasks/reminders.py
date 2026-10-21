from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select

from app.config import settings
from app.database import get_sync_db
from app.models.compliance import ComplianceDocument
from app.models.driver import Driver, DriverStatus
from app.models.maintenance import MaintenanceRecord, MaintenanceStatus
from app.models.vehicle import Vehicle, VehicleStatus
from app.repositories.compliance_repo import applicable_clause, current_ids, status_clause
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
    """Check for expiring driver licenses and medical certificates."""
    db = get_sync_db()
    try:
        today = date.today()
        deadline = today + timedelta(days=30)

        expiring_licenses = db.query(Driver).filter(
            Driver.status == DriverStatus.ACTIVE,
            Driver.license_expiry <= deadline,
            Driver.license_expiry >= today,
        ).all()

        expiring_medicals = db.query(Driver).filter(
            Driver.status == DriverStatus.ACTIVE,
            Driver.medical_expiry <= deadline,
            Driver.medical_expiry >= today,
        ).all()

        return {"expiring_licenses": len(expiring_licenses), "expiring_medicals": len(expiring_medicals)}
    finally:
        db.close()
