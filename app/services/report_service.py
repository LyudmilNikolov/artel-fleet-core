"""Report service for generating fleet analytics and exportable data."""

import datetime as dt
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.i18n import _
from app.models.expense import Expense, ExpenseCategory
from app.models.maintenance import MaintenanceRecord
from app.models.vehicle import Vehicle
from app.utils.export import CSVExporter, ExcelExporter


class ReportService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def tco_report(self, start_date: dt.date | None = None, end_date: dt.date | None = None) -> list[dict]:
        """Total Cost of Ownership per vehicle."""
        query = select(
            Vehicle.id,
            Vehicle.license_plate,
            Vehicle.brand,
            Vehicle.model,
            Vehicle.year,
            Vehicle.vehicle_type,
            Vehicle.purchase_price,
            func.coalesce(func.sum(Expense.amount), 0).label("total_expenses"),
        ).outerjoin(Expense, Expense.vehicle_id == Vehicle.id)
        if start_date:
            query = query.where(Expense.date >= start_date)
        if end_date:
            query = query.where(Expense.date <= end_date)
        query = query.group_by(
            Vehicle.id,
            Vehicle.license_plate,
            Vehicle.brand,
            Vehicle.model,
            Vehicle.year,
            Vehicle.vehicle_type,
            Vehicle.purchase_price,
        ).order_by(func.coalesce(func.sum(Expense.amount), 0).desc())

        result = await self.db.execute(query)
        return [
            {
                "id": str(row[0]),
                "license_plate": row[1],
                "brand": row[2],
                "model": row[3],
                "year": row[4],
                "vehicle_type": row[5].value,
                "purchase_price": Decimal(row[6]) if row[6] is not None else None,
                "total_expenses": Decimal(row[7]),
                "tco": (Decimal(row[6]) if row[6] is not None else Decimal("0")) + Decimal(row[7]),
            }
            for row in result.all()
        ]

    async def fleet_utilization(self) -> dict:
        """Fleet utilization statistics."""
        result = await self.db.execute(select(Vehicle.status, func.count(Vehicle.id)).group_by(Vehicle.status))
        counts = {str(row[0].value): row[1] for row in result.all()}
        total = sum(counts.values())
        active = counts.get("active", 0)
        return {
            "total": total,
            "active": active,
            "in_maintenance": counts.get("in_maintenance", 0),
            "decommissioned": counts.get("decommissioned", 0),
            "reserved": counts.get("reserved", 0),
            "utilization_rate": round(active / total * 100, 1) if total > 0 else 0,
        }

    async def fuel_consumption(self, start_date: dt.date | None = None, end_date: dt.date | None = None) -> list[dict]:
        """Fuel consumption per vehicle."""
        query = (
            select(
                Vehicle.id,
                Vehicle.license_plate,
                Vehicle.brand,
                Vehicle.model,
                func.sum(Expense.fuel_liters).label("total_liters"),
                func.sum(Expense.amount).label("total_cost"),
                func.count(Expense.id).label("refuel_count"),
                Vehicle.vehicle_type,
            )
            .join(Expense, Expense.vehicle_id == Vehicle.id)
            .where(Expense.category == ExpenseCategory.FUEL)
            .where(Expense.fuel_liters.is_not(None))
        )
        if start_date:
            query = query.where(Expense.date >= start_date)
        if end_date:
            query = query.where(Expense.date <= end_date)
        query = query.group_by(
            Vehicle.id, Vehicle.license_plate, Vehicle.brand, Vehicle.model, Vehicle.vehicle_type
        ).order_by(func.sum(Expense.fuel_liters).desc())

        result = await self.db.execute(query)
        return [
            {
                "id": str(row[0]),
                "license_plate": row[1],
                "brand": row[2],
                "model": row[3],
                "total_liters": round(float(row[4]), 1) if row[4] else 0,
                "total_cost": Decimal(row[5]) if row[5] is not None else Decimal("0"),
                "refuel_count": row[6],
                "vehicle_type": row[7].value,
                "avg_price": self.fuel_average(row[5], row[4]),
            }
            for row in result.all()
        ]

    @staticmethod
    def fuel_average(cost, liters) -> Decimal:
        return Decimal(str(cost or 0)) / Decimal(str(liters)) if liters else Decimal("0")

    async def expense_analysis(self, start_date: dt.date | None = None, end_date: dt.date | None = None) -> dict:
        """Comprehensive expense analysis."""
        base_filter = []
        if start_date:
            base_filter.append(Expense.date >= start_date)
        if end_date:
            base_filter.append(Expense.date <= end_date)

        # By category
        cat_result = await self.db.execute(
            select(Expense.category, func.sum(Expense.amount), func.count(Expense.id))
            .where(*base_filter)
            .group_by(Expense.category)
            .order_by(func.sum(Expense.amount).desc())
        )
        by_category = [{"category": str(r[0].value), "total": Decimal(r[1]), "count": r[2]} for r in cat_result.all()]

        # Monthly trends
        monthly_result = await self.db.execute(
            select(
                func.date_trunc("month", Expense.date).label("month"),
                func.sum(Expense.amount),
                func.count(Expense.id),
            )
            .where(*base_filter)
            .group_by("month")
            .order_by("month")
        )
        monthly = [{"month": str(r[0].date()), "total": Decimal(r[1]), "count": r[2]} for r in monthly_result.all()]

        # Grand total
        total_result = await self.db.execute(
            select(func.sum(Expense.amount), func.count(Expense.id)).where(*base_filter)
        )
        row = total_result.one()
        grand_total = Decimal(row[0]) if row[0] is not None else Decimal("0")
        total_count = row[1]

        return {
            "by_category": by_category,
            "monthly": monthly,
            "grand_total": grand_total,
            "total_count": total_count,
        }

    async def maintenance_history(
        self, start_date: dt.date | None = None, end_date: dt.date | None = None
    ) -> list[dict]:
        """Maintenance history report."""
        query = select(
            MaintenanceRecord.id,
            Vehicle.license_plate,
            Vehicle.brand,
            Vehicle.model,
            MaintenanceRecord.type,
            MaintenanceRecord.title,
            MaintenanceRecord.status,
            MaintenanceRecord.scheduled_date,
            MaintenanceRecord.completed_date,
            MaintenanceRecord.cost,
            MaintenanceRecord.service_provider,
            Vehicle.vehicle_type,
        ).join(Vehicle, Vehicle.id == MaintenanceRecord.vehicle_id)

        if start_date:
            query = query.where(MaintenanceRecord.scheduled_date >= start_date)
        if end_date:
            query = query.where(MaintenanceRecord.scheduled_date <= end_date)

        query = query.order_by(MaintenanceRecord.scheduled_date.desc())
        result = await self.db.execute(query)
        return [
            {
                "id": str(r[0]),
                "license_plate": r[1],
                "brand": r[2],
                "model": r[3],
                "type": str(r[4].value),
                "title": r[5],
                "status": str(r[6].value),
                "scheduled_date": str(r[7]) if r[7] else "",
                "completed_date": str(r[8]) if r[8] else "",
                "cost": Decimal(r[9]) if r[9] is not None else None,
                "service_provider": r[10] or "",
                "vehicle_type": r[11].value,
            }
            for r in result.all()
        ]

    # --- Export helpers ---

    async def export_tco_excel(
        self, start_date: dt.date | None = None, end_date: dt.date | None = None, *, locale: str = "bg"
    ) -> bytes:
        data = await self.tco_report(start_date, end_date)
        exp = ExcelExporter((_("report.tco", lang=locale) + " (EUR)"), monetary_columns=(6, 7, 8))
        period = self._period_label(start_date, end_date, locale)
        exp.add_title((_("report.tco", lang=locale) + " (EUR)"), period)
        exp.add_headers(
            [
                _("vehicle.registration", lang=locale),
                _("vehicle.vehicle_type", lang=locale),
                _("vehicle.brand", lang=locale),
                _("vehicle.model", lang=locale),
                _("vehicle.year", lang=locale),
                (_("vehicle.purchase_price", lang=locale) + " (EUR)"),
                (_("report.total_expenses", lang=locale) + " (EUR)"),
                (_("report.tco", lang=locale) + " (EUR)"),
            ]
        )
        for r in data:
            exp.add_row(
                [
                    r["license_plate"],
                    _("vehicle_type." + r["vehicle_type"], lang=locale),
                    r["brand"],
                    r["model"],
                    r["year"],
                    r["purchase_price"],
                    r["total_expenses"],
                    r["tco"],
                ]
            )
        if data:
            exp.add_summary_row(
                [
                    "",
                    "",
                    "",
                    "",
                    _("common.total", lang=locale),
                    sum((r["purchase_price"] or Decimal("0")) for r in data),
                    sum(r["total_expenses"] for r in data),
                    sum(r["tco"] for r in data),
                ]
            )
        return exp.to_bytes()

    async def export_fuel_excel(
        self, start_date: dt.date | None = None, end_date: dt.date | None = None, *, locale: str = "bg"
    ) -> bytes:
        data = await self.fuel_consumption(start_date, end_date)
        exp = ExcelExporter(_("report.fuel", lang=locale), monetary_columns=(6,))
        period = self._period_label(start_date, end_date, locale)
        exp.add_title(_("report.fuel", lang=locale), period)
        exp.add_headers(
            [
                _("vehicle.registration", lang=locale),
                _("vehicle.vehicle_type", lang=locale),
                _("vehicle.brand", lang=locale),
                _("vehicle.model", lang=locale),
                _("report.total_liters", lang=locale),
                (_("report.total_cost", lang=locale) + " (EUR)"),
                _("report.refuel_count", lang=locale),
            ]
        )
        for r in data:
            exp.add_row(
                [
                    r["license_plate"],
                    _("vehicle_type." + r["vehicle_type"], lang=locale),
                    r["brand"],
                    r["model"],
                    r["total_liters"],
                    r["total_cost"],
                    r["refuel_count"],
                ]
            )
        return exp.to_bytes()

    async def export_maintenance_excel(
        self, start_date: dt.date | None = None, end_date: dt.date | None = None, *, locale: str = "bg"
    ) -> bytes:
        data = await self.maintenance_history(start_date, end_date)
        exp = ExcelExporter(_("report.maintenance", lang=locale), monetary_columns=(9,))
        period = self._period_label(start_date, end_date, locale)
        exp.add_title(_("report.maintenance", lang=locale), period)
        exp.add_headers(
            [
                _("vehicle.registration", lang=locale),
                _("vehicle.vehicle_type", lang=locale),
                _("nav.vehicles", lang=locale),
                _("common.type", lang=locale),
                _("common.title", lang=locale),
                _("common.status", lang=locale),
                _("maintenance.scheduled_date", lang=locale),
                _("maintenance.completed_date", lang=locale),
                (_("maintenance.cost", lang=locale) + " (EUR)"),
                _("maintenance.service_provider", lang=locale),
            ]
        )
        for r in data:
            exp.add_row(
                [
                    r["license_plate"],
                    _("vehicle_type." + r["vehicle_type"], lang=locale),
                    f"{r['brand']} {r['model']}",
                    _("maintenance_type." + r["type"], lang=locale),
                    r["title"],
                    _("status." + r["status"], lang=locale),
                    r["scheduled_date"],
                    r["completed_date"],
                    r["cost"],
                    r["service_provider"],
                ]
            )
        return exp.to_bytes()

    async def export_expense_csv(
        self, start_date: dt.date | None = None, end_date: dt.date | None = None, *, locale: str = "bg"
    ) -> bytes:
        analysis = await self.expense_analysis(start_date, end_date)
        exp = CSVExporter()
        exp.add_headers(
            [
                _("common.category", lang=locale),
                (_("report.total_amount", lang=locale) + " (EUR)"),
                _("report.transaction_count", lang=locale),
            ]
        )
        for r in analysis["by_category"]:
            exp.add_row([_("expense_category." + r["category"], lang=locale), r["total"], r["count"]])
        return exp.to_bytes()

    @staticmethod
    def _period_label(start_date: dt.date | None, end_date: dt.date | None, locale: str) -> str:
        if start_date and end_date:
            return _("report.period", lang=locale, start=start_date, end=end_date)
        elif start_date:
            return _("report.from", lang=locale, start=start_date)
        elif end_date:
            return _("report.until", lang=locale, end=end_date)
        return _("report.all_time", lang=locale)
