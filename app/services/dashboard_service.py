"""Dashboard service providing fleet overview and widget data."""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.driver import Driver, DriverStatus
from app.models.expense import Expense
from app.models.maintenance import MaintenanceRecord, MaintenanceStatus
from app.models.vehicle import Vehicle
from app.services.compliance_service import ComplianceService
from app.services.driver_document_service import DriverDocumentService
from app.services.service_due_service import ServiceDueService
from app.services.validity import utc_today


class DashboardService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def fleet_overview(self) -> dict:
        """Get vehicle counts by status."""
        result = await self.db.execute(select(Vehicle.status, func.count(Vehicle.id)).group_by(Vehicle.status))
        counts = {str(row[0].value): row[1] for row in result.all()}
        total = sum(counts.values())
        return {
            "total": total,
            "active": counts.get("active", 0),
            "in_maintenance": counts.get("in_maintenance", 0),
            "decommissioned": counts.get("decommissioned", 0),
            "reserved": counts.get("reserved", 0),
        }

    async def attention_needed(self) -> dict:
        """Get items needing attention: overdue maintenance, compliance deadlines, driver docs."""
        today = utc_today()
        upcoming_until = today + timedelta(days=settings.MAINTENANCE_UPCOMING_DAYS)

        # Overdue maintenance
        overdue_maint = await self.db.execute(
            select(func.count(MaintenanceRecord.id)).where(
                MaintenanceRecord.status == MaintenanceStatus.SCHEDULED,
                MaintenanceRecord.scheduled_date < today,
            )
        )
        overdue_maintenance = overdue_maint.scalar() or 0

        # Upcoming maintenance (next 14 days)
        upcoming_maint = await self.db.execute(
            select(func.count(MaintenanceRecord.id)).where(
                MaintenanceRecord.status == MaintenanceStatus.SCHEDULED,
                MaintenanceRecord.scheduled_date >= today,
                MaintenanceRecord.scheduled_date <= upcoming_until,
            )
        )
        upcoming_maintenance = upcoming_maint.scalar() or 0

        # Planned service items (engine oil, ...) overdue or due soon by km or date
        service = await ServiceDueService(self.db).attention_counts()

        # Current vehicle compliance documents expiring soon / expired (computed from end_date)
        compliance = await ComplianceService(self.db).attention_counts()
        expiring_compliance = compliance["expiring_soon"]
        expired_compliance = compliance["expired"]

        # Current driver documents (licence, ID card, ...) of active drivers, computed from end_date
        drivers = await DriverDocumentService(self.db).attention_counts()
        expiring_driver_documents = drivers["expiring_soon"]
        expired_driver_documents = drivers["expired"]

        return {
            "overdue_maintenance": overdue_maintenance,
            "upcoming_maintenance": upcoming_maintenance,
            "overdue_service": service["overdue"],
            "due_soon_service": service["due_soon"],
            "expiring_compliance": expiring_compliance,
            "expired_compliance": expired_compliance,
            "expiring_driver_documents": expiring_driver_documents,
            "expired_driver_documents": expired_driver_documents,
            "total_alerts": overdue_maintenance
            + service["overdue"]
            + service["due_soon"]
            + expiring_compliance
            + expired_compliance
            + expiring_driver_documents
            + expired_driver_documents,
        }

    async def expense_summary(self, months: int = 6) -> dict:
        """Get expense summary by month and category for last N months."""
        today = date.today()
        start_date = today.replace(day=1) - timedelta(days=months * 30)

        # Monthly totals
        result = await self.db.execute(
            select(
                func.date_trunc("month", Expense.date).label("month"),
                func.sum(Expense.amount).label("total"),
            )
            .where(Expense.date >= start_date)
            .group_by("month")
            .order_by("month")
        )
        monthly = [{"month": str(row[0].date()), "total": Decimal(row[1])} for row in result.all()]

        # Category breakdown
        cat_result = await self.db.execute(
            select(
                Expense.category,
                func.sum(Expense.amount).label("total"),
            )
            .where(Expense.date >= start_date)
            .group_by(Expense.category)
            .order_by(func.sum(Expense.amount).desc())
        )
        categories = [{"category": str(row[0].value), "total": Decimal(row[1])} for row in cat_result.all()]

        # Total
        total_result = await self.db.execute(select(func.sum(Expense.amount)).where(Expense.date >= start_date))
        total = Decimal(total_result.scalar() or 0)

        return {
            "monthly": monthly,
            "categories": categories,
            "total": total,
            "period_months": months,
        }

    async def maintenance_stats(self) -> dict:
        """Get maintenance statistics for kanban-style overview."""
        result = await self.db.execute(
            select(MaintenanceRecord.status, func.count(MaintenanceRecord.id)).group_by(MaintenanceRecord.status)
        )
        counts = {str(row[0].value): row[1] for row in result.all()}
        return {
            "scheduled": counts.get("scheduled", 0),
            "in_progress": counts.get("in_progress", 0),
            "completed": counts.get("completed", 0),
            "cancelled": counts.get("cancelled", 0),
        }

    async def recent_maintenance(self, limit: int = 5) -> list[dict]:
        """Get recent maintenance records."""
        result = await self.db.execute(
            select(MaintenanceRecord).order_by(MaintenanceRecord.created_at.desc()).limit(limit)
        )
        records = result.scalars().all()
        return [
            {
                "id": str(r.id),
                "title": r.title,
                "status": r.status.value,
                "type": r.type.value,
                "scheduled_date": str(r.scheduled_date) if r.scheduled_date else None,
                "vehicle_id": str(r.vehicle_id),
            }
            for r in records
        ]

    async def driver_stats(self) -> dict:
        """Get driver statistics."""
        result = await self.db.execute(select(Driver.status, func.count(Driver.id)).group_by(Driver.status))
        counts = {str(row[0].value): row[1] for row in result.all()}
        total = sum(counts.values())
        return {
            "total": total,
            "active": counts.get("active", 0),
            "on_leave": counts.get("on_leave", 0),
            "terminated": counts.get("terminated", 0),
        }

    async def top_expensive_vehicles(self, limit: int = 5) -> list[dict]:
        """Get top vehicles by total expense."""
        result = await self.db.execute(
            select(
                Vehicle.id,
                Vehicle.license_plate,
                Vehicle.brand,
                Vehicle.model,
                func.sum(Expense.amount).label("total_cost"),
            )
            .join(Expense, Expense.vehicle_id == Vehicle.id)
            .group_by(Vehicle.id, Vehicle.license_plate, Vehicle.brand, Vehicle.model)
            .order_by(func.sum(Expense.amount).desc())
            .limit(limit)
        )
        return [
            {
                "id": str(row[0]),
                "license_plate": row[1],
                "brand": row[2],
                "model": row[3],
                "total_cost": Decimal(row[4]),
            }
            for row in result.all()
        ]
