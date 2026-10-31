"""Report service for generating fleet analytics and exportable data."""

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.i18n import _
from app.models.vehicle import Vehicle
from app.repositories.report_repo import ReportRepository
from app.schemas.report import MaintenanceReportFilters, ReportFilters
from app.services.errors import DomainError
from app.utils.export import CSVExporter, ExcelExporter
from app.vehicle_rules import vehicle_display_label


class ReportService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = ReportRepository(db)

    async def tco_report(self, filters: ReportFilters | None = None) -> list[dict]:
        """Total Cost of Ownership per vehicle for the filtered vehicles and expense period."""
        rows = await self.repo.tco(filters or ReportFilters())
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
            for row in rows
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

    async def fuel_consumption(self, filters: ReportFilters | None = None) -> list[dict]:
        """Fuel consumption per vehicle, with L/100 km for mileage-tracked vehicles."""
        filters = filters or ReportFilters()
        rows = await self.repo.fuel(filters)
        efficiency = await self.repo.fuel_efficiency(filters)
        result = []
        for row in rows:
            liters, distance, refuels = efficiency.get(row[0], (None, None, 0))
            l100 = self.l_per_100km(liters, distance, refuels)
            result.append(
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
                    "l_per_100km": l100,
                    # inputs of the fleet-wide figure (page footer); not part of the API schema
                    "efficiency_liters": Decimal(str(liters)) if l100 is not None else None,
                    "distance_km": int(distance) if l100 is not None else None,
                }
            )
        return result

    @staticmethod
    def l_per_100km(liters, distance, refuels) -> Decimal | None:
        """Full-to-full consumption: liters excluding the first refuel over the distance covered.

        None without at least two refuels with a mileage reading or without positive distance.
        """
        if refuels is None or refuels < 2 or not distance or distance <= 0 or liters is None:
            return None
        value = Decimal(str(liters)) * 100 / Decimal(distance)
        return value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)

    @staticmethod
    def fuel_average(cost, liters) -> Decimal:
        return Decimal(str(cost or 0)) / Decimal(str(liters)) if liters else Decimal("0")

    async def expense_analysis(self, filters: ReportFilters | None = None) -> dict:
        """Comprehensive expense analysis for the filtered vehicles and period."""
        filters = filters or ReportFilters()
        by_category = [
            {"category": str(r[0].value), "total": Decimal(r[1]), "count": r[2]}
            for r in await self.repo.expense_by_category(filters)
        ]
        monthly = [
            {"month": str(r[0].date()), "total": Decimal(r[1]), "count": r[2]}
            for r in await self.repo.expense_monthly(filters)
        ]
        total, count = await self.repo.expense_total(filters)
        return {
            "by_category": by_category,
            "monthly": monthly,
            "grand_total": Decimal(total) if total is not None else Decimal("0"),
            "total_count": count,
        }

    @staticmethod
    def _maintenance_row(r) -> dict:
        return {
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

    async def maintenance_history(self, filters: MaintenanceReportFilters | None = None) -> list[dict]:
        """Maintenance history (service date = completed date, else scheduled date); all matching records."""
        rows = await self.repo.maintenance(filters or MaintenanceReportFilters())
        return [self._maintenance_row(r) for r in rows]

    async def maintenance_page(self, filters: MaintenanceReportFilters | None, page: int, size: int) -> dict:
        """One page of the maintenance report plus totals over the whole filtered set (cancelled cost excluded)."""
        filters = filters or MaintenanceReportFilters()
        count, total_cost, completed = await self.repo.maintenance_totals(filters)
        rows = await self.repo.maintenance(filters, offset=(page - 1) * size, limit=size)
        return {
            "rows": [self._maintenance_row(r) for r in rows],
            "total": count,
            "total_cost": total_cost,
            "completed": completed,
            "pages": max(1, -(-count // size)),
        }

    async def selected_vehicle(self, filters: ReportFilters):
        """The vehicle chosen in the filters; raises DomainError when it does not exist."""
        if not filters.vehicle_id:
            return None
        vehicle = await self.repo.vehicle(filters.vehicle_id)
        if vehicle is None:
            raise DomainError("error.report_vehicle_not_found")
        return vehicle

    async def vehicle_mismatch(self, filters: ReportFilters) -> bool:
        """True when the chosen vehicle is outside the chosen vehicle type group (the report is empty)."""
        return bool(filters.vehicle_id and filters.vehicle_type) and not await self.repo.vehicle_group_matches(filters)

    async def filter_label(self, filters: ReportFilters | None, locale: str) -> str:
        """Localized one-line description of the vehicle / type / status filters (empty when none)."""
        if filters is None:
            return ""
        parts = []
        if filters.vehicle_id:
            vehicle = await self.repo.vehicle(filters.vehicle_id)
            parts.append(
                _("report.filter_vehicle", lang=locale) + ": " + (vehicle_display_label(vehicle) if vehicle else "?")
            )
        if filters.vehicle_type:
            parts.append(
                _("report.filter_type", lang=locale) + ": " + _("vehicle_type." + filters.vehicle_type, lang=locale)
            )
        status = getattr(filters, "status", None)
        if status:
            parts.append(_("report.filter_status", lang=locale) + ": " + _("status." + status.value, lang=locale))
        category = getattr(filters, "category", None)
        if category:
            parts.append(
                _("report.filter_category", lang=locale) + ": " + _("expense_category." + category.value, lang=locale)
            )
        return "; ".join(parts)

    async def _subtitle(self, filters: ReportFilters | None, locale: str) -> str:
        period = self._period_label(
            filters.start_date if filters else None, filters.end_date if filters else None, locale
        )
        extra = await self.filter_label(filters, locale)
        return f"{period} | {extra}" if extra else period

    # --- Export helpers ---

    async def export_tco_excel(self, filters: ReportFilters | None = None, *, locale: str = "bg") -> bytes:
        """Excel export of the tco report for exactly the on-screen filters."""
        data = await self.tco_report(filters)
        exp = ExcelExporter((_("report.tco", lang=locale) + " (EUR)"), monetary_columns=(6, 7, 8))
        period = await self._subtitle(filters, locale)
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

    async def export_fuel_excel(self, filters: ReportFilters | None = None, *, locale: str = "bg") -> bytes:
        """Excel export of the fuel report for exactly the on-screen filters."""
        data = await self.fuel_consumption(filters)
        exp = ExcelExporter(_("report.fuel", lang=locale), monetary_columns=(6,))
        period = await self._subtitle(filters, locale)
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
                _("reports.l_per_100km", lang=locale),
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
                    r["l_per_100km"],
                ]
            )
        return exp.to_bytes()

    async def export_maintenance_excel(
        self, filters: MaintenanceReportFilters | None = None, *, locale: str = "bg"
    ) -> bytes:
        """Excel export of the maintenance report for exactly the on-screen filters."""
        data = await self.maintenance_history(filters)
        exp = ExcelExporter(_("report.maintenance", lang=locale), monetary_columns=(9,))
        period = await self._subtitle(filters, locale)
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

    async def export_expense_csv(self, filters: ReportFilters | None = None, *, locale: str = "bg") -> bytes:
        """CSV export of the expense categories for exactly the on-screen filters."""
        analysis = await self.expense_analysis(filters)
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
