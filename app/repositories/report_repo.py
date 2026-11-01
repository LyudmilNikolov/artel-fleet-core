"""Report queries shared by the web pages, API and exports."""

from decimal import Decimal

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.expense import Expense, ExpenseCategory
from app.models.maintenance import MaintenanceRecord, MaintenanceStatus
from app.models.vehicle import Vehicle
from app.schemas.report import MaintenanceReportFilters, ReportFilters
from app.vehicle_rules import MILEAGE_TYPES, VehicleType


def vehicle_conditions(f: ReportFilters) -> list:
    """Vehicle and type-group conditions; both apply together (AND)."""
    conditions = []
    if f.vehicle_id:
        conditions.append(Vehicle.id == f.vehicle_id)
    if f.vehicle_types:
        conditions.append(Vehicle.vehicle_type.in_(f.vehicle_types))
    return conditions


def date_conditions(column, f: ReportFilters) -> list:
    """Inclusive start / end date conditions on a date expression."""
    conditions = []
    if f.start_date:
        conditions.append(column >= f.start_date)
    if f.end_date:
        conditions.append(column <= f.end_date)
    return conditions


class ReportRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def tco(self, f: ReportFilters) -> list[tuple]:
        """Per-vehicle purchase price, expenses and maintenance; vehicles without costs in the period are kept."""
        expense_total = func.coalesce(func.sum(Expense.amount), 0)
        # Maintenance is summed in its own subquery so it cannot be multiplied by the expense join.
        # Same rules as the maintenance report: cancelled records are ignored and the period applies
        # to the completed date (scheduled date while not completed).
        maintenance_total = (
            select(func.coalesce(func.sum(MaintenanceRecord.cost), 0))
            .where(
                MaintenanceRecord.vehicle_id == Vehicle.id,
                MaintenanceRecord.status != MaintenanceStatus.CANCELLED,
                *date_conditions(self.service_date(), f),
            )
            .scalar_subquery()
        )
        query = (
            select(
                Vehicle.id,
                Vehicle.license_plate,
                Vehicle.brand,
                Vehicle.model,
                Vehicle.year,
                Vehicle.vehicle_type,
                Vehicle.purchase_price,
                expense_total.label("total_expenses"),
                maintenance_total.label("maintenance_cost"),
            )
            .select_from(Vehicle)
            .outerjoin(Expense, and_(Expense.vehicle_id == Vehicle.id, *date_conditions(Expense.date, f)))
            .where(*vehicle_conditions(f))
            .group_by(
                Vehicle.id,
                Vehicle.license_plate,
                Vehicle.brand,
                Vehicle.model,
                Vehicle.year,
                Vehicle.vehicle_type,
                Vehicle.purchase_price,
            )
            .order_by((expense_total + maintenance_total).desc(), Vehicle.license_plate)
        )
        return list((await self.db.execute(query)).all())

    def _fuel_conditions(self, f: ReportFilters) -> list:
        return [
            Expense.category == ExpenseCategory.FUEL,
            Expense.fuel_liters.is_not(None),
            *date_conditions(Expense.date, f),
            *vehicle_conditions(f),
        ]

    async def fuel(self, f: ReportFilters) -> list[tuple]:
        """Fuel liters, cost and refuel count per vehicle."""
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
            .where(*self._fuel_conditions(f))
            .group_by(Vehicle.id, Vehicle.license_plate, Vehicle.brand, Vehicle.model, Vehicle.vehicle_type)
            .order_by(func.sum(Expense.fuel_liters).desc(), Vehicle.license_plate)
        )
        return list((await self.db.execute(query)).all())

    async def fuel_efficiency(self, f: ReportFilters) -> dict:
        """Full-to-full inputs per mileage-tracked vehicle: liters without the first refuel, distance, refuels.

        Only refuels with ``mileage_at_refuel`` count. Returns {vehicle_id: (liters, distance_km, refuels)}.
        """
        tracked = [VehicleType(t) for t in MILEAGE_TYPES]
        ranked = (
            select(
                Expense.vehicle_id.label("vid"),
                Expense.fuel_liters.label("liters"),
                Expense.mileage_at_refuel.label("km"),
                func.row_number()
                .over(
                    partition_by=Expense.vehicle_id,
                    order_by=(Expense.mileage_at_refuel, Expense.date, Expense.id),
                )
                .label("rn"),
            )
            .join(Vehicle, Vehicle.id == Expense.vehicle_id)
            .where(
                *self._fuel_conditions(f),
                Expense.mileage_at_refuel.is_not(None),
                Vehicle.vehicle_type.in_(tracked),
            )
            .subquery()
        )
        query = select(
            ranked.c.vid,
            func.sum(case((ranked.c.rn > 1, ranked.c.liters), else_=0)),
            func.max(ranked.c.km) - func.min(ranked.c.km),
            func.count(),
        ).group_by(ranked.c.vid)
        return {row[0]: (row[1], row[2], row[3]) for row in (await self.db.execute(query)).all()}

    def _expense_scope(self, query, f: ReportFilters):
        """Apply date, category and (only when needed) vehicle filters to an aggregate over expenses."""
        vehicle = vehicle_conditions(f)
        if vehicle:
            query = query.join(Vehicle, Vehicle.id == Expense.vehicle_id).where(*vehicle)
        category = getattr(f, "category", None)
        if category:
            query = query.where(Expense.category == category)
        return query.where(*date_conditions(Expense.date, f))

    async def expense_by_category(self, f: ReportFilters) -> list[tuple]:
        query = self._expense_scope(select(Expense.category, func.sum(Expense.amount), func.count(Expense.id)), f)
        query = query.group_by(Expense.category).order_by(func.sum(Expense.amount).desc(), Expense.category)
        return list((await self.db.execute(query)).all())

    async def expense_monthly(self, f: ReportFilters) -> list[tuple]:
        month = func.date_trunc("month", Expense.date).label("month")
        query = self._expense_scope(select(month, func.sum(Expense.amount), func.count(Expense.id)), f)
        query = query.group_by(month).order_by(month)
        return list((await self.db.execute(query)).all())

    async def expense_total(self, f: ReportFilters) -> tuple:
        query = self._expense_scope(select(func.sum(Expense.amount), func.count(Expense.id)), f)
        return (await self.db.execute(query)).one()

    @staticmethod
    def service_date():
        """Date a maintenance record belongs to: completed date, else scheduled date."""
        return func.coalesce(MaintenanceRecord.completed_date, MaintenanceRecord.scheduled_date)

    def _maintenance_conditions(self, f: MaintenanceReportFilters) -> list:
        conditions = [*date_conditions(self.service_date(), f), *vehicle_conditions(f)]
        if getattr(f, "status", None):
            conditions.append(MaintenanceRecord.status == f.status)
        return conditions

    async def maintenance(self, f: MaintenanceReportFilters, offset: int = 0, limit: int | None = None) -> list[tuple]:
        """Maintenance records, newest service date first."""
        query = (
            select(
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
            )
            .join(Vehicle, Vehicle.id == MaintenanceRecord.vehicle_id)
            .where(*self._maintenance_conditions(f))
            .order_by(self.service_date().desc().nulls_last(), MaintenanceRecord.id)
            .offset(offset)
        )
        if limit is not None:
            query = query.limit(limit)
        return list((await self.db.execute(query)).all())

    async def maintenance_totals(self, f: MaintenanceReportFilters) -> tuple[int, Decimal, int]:
        """(records, cost excluding cancelled, completed records) over the whole filtered set."""
        cost = func.coalesce(
            func.sum(case((MaintenanceRecord.status != MaintenanceStatus.CANCELLED, MaintenanceRecord.cost), else_=0)),
            0,
        )
        completed = func.count(case((MaintenanceRecord.status == MaintenanceStatus.COMPLETED, 1)))
        query = (
            select(func.count(MaintenanceRecord.id), cost, completed)
            .select_from(MaintenanceRecord)
            .join(Vehicle, Vehicle.id == MaintenanceRecord.vehicle_id)
            .where(*self._maintenance_conditions(f))
        )
        count, total, done = (await self.db.execute(query)).one()
        return count, Decimal(total), done

    async def vehicle_group_matches(self, f: ReportFilters) -> bool:
        """False when a chosen vehicle does not exist inside the chosen type group."""
        if not f.vehicle_id:
            return True
        query = select(Vehicle.id).where(*vehicle_conditions(f))
        return (await self.db.execute(query.limit(1))).first() is not None

    async def vehicle(self, vehicle_id) -> Vehicle | None:
        return await self.db.get(Vehicle, vehicle_id)
