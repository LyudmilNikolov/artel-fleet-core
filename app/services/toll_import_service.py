"""Bulk import of toll transactions from the Shell Priced Transaction CSV.

Flow: ``preview`` reads the file and saves a batch that says what would happen; ``apply`` re-checks that against
the current data and writes it. Uploading a file that overlaps an earlier one is safe:

- a transaction already imported with the same vehicle, date and amount is skipped (a newly issued invoice number
  is copied onto it);
- a transaction already imported with a different vehicle, date or amount is reported, never changed silently;
- new transactions of one vehicle and day go into one toll expense: an earlier imported expense of that day grows
  by their amount, otherwise a new expense is created;
- lines that can not be imported (unknown plate, trailer, decommissioned vehicle, missing or odd values) block only
  themselves. They are kept with the batch, and stay listed as waiting until a later upload imports them.
"""

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.i18n import _
from app.models.audit_log import AuditAction
from app.models.expense import Expense, ExpenseCategory
from app.models.notification import Notification, NotificationType
from app.models.toll_import import TollImportBatch, TollImportStatus, TollTransaction
from app.models.user import User
from app.models.vehicle import Vehicle, VehicleStatus
from app.schemas.expense import ExpenseCreate, ExpenseUpdate
from app.services.audit_service import AuditService
from app.services.errors import DomainError
from app.services.expense_service import ExpenseService
from app.utils import shell_toll_csv
from app.utils.plates import plate_key
from app.utils.shell_toll_csv import SOURCE, TollRow
from app.vehicle_rules import validate_expense_category

# Reasons a line was not imported (labels: toll_import.reason.* in i18n)
REASONS = (
    "missing_field",
    "bad_date",
    "bad_amount",
    "not_eur",
    "refund",
    "conflict_in_file",
    "changed",
    "unknown_vehicle",
    "ambiguous_vehicle",
    "not_applicable",
    "decommissioned",
)
WAITING_BATCHES = 50  # how many recent uploads the "still waiting" list looks at
ZERO = Decimal("0.00")


@dataclass
class Plan:
    """What a file would do to the current data."""

    new: list[tuple[TollRow, Vehicle]] = field(default_factory=list)
    duplicates: int = 0
    invoice_updates: dict[UUID, str] = field(default_factory=dict)  # TollTransaction.id -> invoice number
    invoiced: list[dict] = field(default_factory=list)  # the same updates, as shown on the import page
    problems: list[dict] = field(default_factory=list)
    groups: dict[tuple[UUID, dt.date], list[TollRow]] = field(default_factory=dict)
    existing_expenses: dict[tuple[UUID, dt.date], Expense] = field(default_factory=dict)
    period: tuple[dt.date | None, dt.date | None] = (None, None)
    total_rows: int = 0

    def summary(self) -> dict:
        new_amount = sum((row.amount for row, _vehicle in self.new), ZERO)
        problem_amount = sum((Decimal(p["amount"]) for p in self.problems if _is_amount(p.get("amount"))), ZERO)
        return {
            "total_rows": self.total_rows,
            "new_rows": len(self.new),
            "new_amount": str(new_amount),
            "duplicate_rows": self.duplicates,
            "problem_rows": len(self.problems),
            "problem_amount": str(problem_amount),
            "vehicles": len({vehicle.id for _row, vehicle in self.new}),
            "expenses_created": sum(1 for key in self.groups if key not in self.existing_expenses),
            "expenses_updated": sum(1 for key in self.groups if key in self.existing_expenses),
            "invoice_updates": len(self.invoice_updates),
            "invoiced": self.invoiced,
            "period_start": self.period[0].isoformat() if self.period[0] else None,
            "period_end": self.period[1].isoformat() if self.period[1] else None,
        }


def _is_amount(value) -> bool:
    try:
        Decimal(value)
    except Exception:  # noqa: BLE001 - not a number
        return False
    return True


def _problem(row: TollRow, reason: str, field_name: str | None = None) -> dict:
    return {
        "line": row.line,
        "transaction_id": row.transaction_id,
        "plate": row.plate,
        "date": row.date.isoformat() if row.date else row.raw.get("date"),
        "amount": str(row.amount) if row.amount is not None else None,
        "amount_text": None if row.amount is not None else row.raw.get("amount"),
        "reason": reason,
        "field": field_name,
    }


def _row_to_json(row: TollRow) -> dict:
    return {
        "line": row.line,
        "transaction_id": row.transaction_id,
        "plate": row.plate,
        "date": row.date.isoformat(),
        "time": row.time.isoformat() if row.time else None,
        "amount": str(row.amount),
        "currency": row.currency,
        "product": row.product,
        "network": row.network,
        "invoice_number": row.invoice_number,
    }


def _row_from_json(data: dict) -> TollRow:
    return TollRow(
        line=data["line"],
        transaction_id=data["transaction_id"],
        plate=data["plate"],
        date=dt.date.fromisoformat(data["date"]),
        time=dt.time.fromisoformat(data["time"]) if data.get("time") else None,
        amount=Decimal(data["amount"]),
        currency=data.get("currency") or "EUR",
        product=data.get("product"),
        network=data.get("network"),
        invoice_number=data.get("invoice_number"),
        raw={"refund": None},
    )


class TollImportService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ---- reading -------------------------------------------------------------------------------------------

    async def get(self, batch_id: UUID) -> TollImportBatch | None:
        return await self.session.get(TollImportBatch, batch_id)

    async def list_batches(self, page: int = 1, size: int = 20) -> tuple[list[TollImportBatch], int]:
        """Applied uploads, newest first."""
        condition = TollImportBatch.status == TollImportStatus.APPLIED
        total = await self.session.scalar(select(func.count()).select_from(TollImportBatch).where(condition)) or 0
        result = await self.session.scalars(
            select(TollImportBatch)
            .where(condition)
            .order_by(TollImportBatch.applied_at.desc(), TollImportBatch.id)
            .offset((page - 1) * size)
            .limit(size)
        )
        return list(result.all()), total

    async def imported_ids(self, transaction_ids) -> set[str]:
        ids = [t for t in set(transaction_ids) if t]
        if not ids:
            return set()
        result = await self.session.scalars(
            select(TollTransaction.external_id).where(
                TollTransaction.source == SOURCE, TollTransaction.external_id.in_(ids)
            )
        )
        return set(result.all())

    async def waiting(self) -> list[dict]:
        """Lines of recent uploads that are still not imported, newest report per transaction.

        A line is resolved as soon as any later upload imported its transaction (after the vehicle was added, for
        instance). Lines without a transaction ID can not be tracked and only show on their own upload.
        """
        batches, _total = await self.list_batches(size=WAITING_BATCHES)
        latest: dict[str, dict] = {}
        for batch in batches:  # newest first
            for problem in batch.problems or []:
                tid = problem.get("transaction_id")
                if tid and tid not in latest:
                    latest[tid] = {**problem, "batch_id": str(batch.id), "filename": batch.filename}
        imported = await self.imported_ids(latest)
        rows = [p for tid, p in latest.items() if tid not in imported]
        return sorted(rows, key=lambda p: (p.get("plate") or "", p.get("date") or "", p.get("line") or 0))

    # ---- checking ------------------------------------------------------------------------------------------

    async def _vehicles_by_plate(self) -> dict[str, list[Vehicle]]:
        vehicles = defaultdict(list)
        for vehicle in (await self.session.scalars(select(Vehicle))).all():
            vehicles[plate_key(vehicle.license_plate)].append(vehicle)
        return vehicles

    async def _known(self, rows: list[TollRow]) -> dict[str, TollTransaction]:
        ids = list({row.transaction_id for row in rows if row.transaction_id})
        known: dict[str, TollTransaction] = {}
        for start in range(0, len(ids), 5000):
            result = await self.session.scalars(
                select(TollTransaction).where(
                    TollTransaction.source == SOURCE, TollTransaction.external_id.in_(ids[start : start + 5000])
                )
            )
            known.update({t.external_id: t for t in result.all()})
        return known

    async def _expenses_for(self, groups) -> dict[tuple[UUID, dt.date], Expense]:
        """Earlier imported toll expenses of the same vehicles and days (a typed-in expense is never merged)."""
        if not groups:
            return {}
        vehicle_ids = {vehicle_id for vehicle_id, _day in groups}
        days = [day for _vehicle_id, day in groups]
        result = await self.session.scalars(
            select(Expense)
            .where(
                Expense.vehicle_id.in_(vehicle_ids),
                Expense.category == ExpenseCategory.TOLL,
                Expense.date >= min(days),
                Expense.date <= max(days),
                Expense.id.in_(select(TollTransaction.expense_id).where(TollTransaction.source == SOURCE)),
            )
            .order_by(Expense.created_at, Expense.id)
        )
        found: dict[tuple[UUID, dt.date], Expense] = {}
        for expense in result.unique().all():
            found.setdefault((expense.vehicle_id, expense.date), expense)
        return found

    async def plan(self, rows: list[TollRow]) -> Plan:
        """Sort every line into new / already imported / problem, against the data as it is now."""
        plan = Plan(total_rows=len(rows))
        dates = [row.date for row in rows if row.date]
        plan.period = (min(dates), max(dates)) if dates else (None, None)
        vehicles = await self._vehicles_by_plate()
        known = await self._known(rows)
        expense_vehicle = {}
        if known:
            expense_ids = {t.expense_id for t in known.values()}
            result = await self.session.execute(
                select(Expense.id, Expense.vehicle_id).where(Expense.id.in_(expense_ids))
            )
            expense_vehicle = dict(result.all())
        in_file: dict[str, TollRow] = {}

        for row in rows:
            if row.problem:
                plan.problems.append(_problem(row, row.problem, row.problem_field))
                continue
            key = plate_key(row.plate)
            earlier = in_file.get(row.transaction_id)
            if earlier is not None:
                same = (plate_key(earlier.plate), earlier.date, earlier.amount) == (key, row.date, row.amount)
                if same:
                    plan.duplicates += 1
                else:
                    plan.problems.append(_problem(row, "conflict_in_file"))
                continue
            in_file[row.transaction_id] = row

            stored = known.get(row.transaction_id)
            if stored is not None:
                matches = [v.id for v in vehicles.get(key, [])]
                same_vehicle = expense_vehicle.get(stored.expense_id) in matches or plate_key(stored.plate) == key
                if same_vehicle and stored.transaction_date == row.date and stored.amount == row.amount:
                    plan.duplicates += 1
                    if row.invoice_number and row.invoice_number != stored.invoice_number:
                        plan.invoice_updates[stored.id] = row.invoice_number
                        plan.invoiced.append(
                            {
                                "transaction_id": row.transaction_id,
                                "plate": row.plate,
                                "date": row.date.isoformat(),
                                "amount": str(row.amount),
                                "old_invoice": stored.invoice_number,
                                "invoice": row.invoice_number,
                                "expense_id": str(stored.expense_id),
                            }
                        )
                else:
                    plan.problems.append(_problem(row, "changed"))
                continue

            candidates = vehicles.get(key, [])
            if not candidates:
                plan.problems.append(_problem(row, "unknown_vehicle"))
                continue
            if len(candidates) > 1:
                plan.problems.append(_problem(row, "ambiguous_vehicle"))
                continue
            vehicle = candidates[0]
            try:
                validate_expense_category(vehicle.vehicle_type, ExpenseCategory.TOLL)
            except ValueError:
                plan.problems.append(_problem(row, "not_applicable"))
                continue
            if vehicle.status == VehicleStatus.DECOMMISSIONED:
                plan.problems.append(_problem(row, "decommissioned"))
                continue
            plan.new.append((row, vehicle))

        groups: dict[tuple[UUID, dt.date], list[TollRow]] = defaultdict(list)
        for row, vehicle in plan.new:
            groups[(vehicle.id, row.date)].append(row)
        plan.groups = dict(groups)
        plan.existing_expenses = await self._expenses_for(plan.groups)
        return plan

    # ---- writing -------------------------------------------------------------------------------------------

    async def preview(self, data: bytes, filename: str, user: User) -> TollImportBatch:
        """Read the file and save what importing it would do; nothing is imported yet."""
        rows = shell_toll_csv.parse(data)
        if not rows:
            raise DomainError("error.toll_import_empty")
        plan = await self.plan(rows)
        batch = TollImportBatch(
            source=SOURCE,
            filename=filename,
            status=TollImportStatus.PREVIEW,
            uploaded_by=user.id,
            rows=[_row_to_json(row) for row in rows if not row.problem],
            problems=plan.problems,
            summary=plan.summary(),
        )
        self.session.add(batch)
        await self.session.flush()
        return batch

    async def apply(self, batch_id: UUID, user: User) -> TollImportBatch:
        """Import a previewed upload. Checked again first, so a vehicle added since the preview is used."""
        batch = await self.session.scalar(
            select(TollImportBatch)
            .where(TollImportBatch.id == batch_id)
            .with_for_update(of=TollImportBatch)
            .execution_options(populate_existing=True)
        )
        if batch is None:
            raise DomainError("error.toll_import_not_found")
        if batch.status != TollImportStatus.PREVIEW:
            raise DomainError("error.toll_import_already_applied")
        rows = [_row_from_json(r) for r in batch.rows or []]
        file_problems = [
            p for p in batch.problems if p["reason"] in ("missing_field", "bad_date", "bad_amount", "not_eur", "refund")
        ]
        plan = await self.plan(rows)
        plan.problems = sorted(file_problems + plan.problems, key=lambda p: p["line"])
        plan.total_rows = batch.summary.get("total_rows", len(rows))

        expenses = ExpenseService(self.session)
        try:
            for (vehicle_id, day), group in plan.groups.items():
                total = sum((row.amount for row in group), ZERO)
                expense = plan.existing_expenses.get((vehicle_id, day))
                if expense is not None:
                    expense = await expenses.update(expense.id, ExpenseUpdate(amount=expense.amount + total))
                else:
                    first = group[0]
                    products = sorted({row.product for row in group if row.product})
                    expense = await expenses.create(
                        ExpenseCreate(
                            vehicle_id=vehicle_id,
                            category=ExpenseCategory.TOLL,
                            amount=total,
                            date=day,
                            vendor=first.network,
                            description=", ".join(products) or None,
                        ),
                        user.id,
                    )
                for row in group:
                    self.session.add(
                        TollTransaction(
                            source=SOURCE,
                            external_id=row.transaction_id,
                            expense_id=expense.id,
                            import_batch_id=batch.id,
                            plate=row.plate[:20],
                            transaction_date=row.date,
                            transaction_time=row.time,
                            amount=row.amount,
                            product=(row.product or None) and row.product[:100],
                            network=(row.network or None) and row.network[:200],
                            invoice_number=(row.invoice_number or None) and row.invoice_number[:50],
                        )
                    )
            for transaction_id, invoice in plan.invoice_updates.items():
                stored = await self.session.get(TollTransaction, transaction_id)
                stored.invoice_number = invoice[:50]
            await self.session.flush()
        except IntegrityError as exc:  # another upload imported the same transactions meanwhile
            raise DomainError("error.toll_import_conflict") from exc

        batch.status = TollImportStatus.APPLIED
        batch.applied_at = dt.datetime.now(dt.UTC)
        batch.rows = None
        batch.problems = plan.problems
        batch.summary = plan.summary()
        await AuditService(self.session).log(
            user_id=user.id,
            action=AuditAction.CREATE,
            entity_type="expense_import",
            entity_id=batch.id,
            changes={"filename": batch.filename, **batch.summary},
        )
        if plan.problems:
            self._notify(batch, user)
        await self.session.flush()
        return batch

    def _notify(self, batch: TollImportBatch, user: User) -> None:
        """Tell the uploader that some lines are waiting, with the plates that are missing."""
        lang = user.language or "bg"
        unknown = sorted({p["plate"] for p in batch.problems if p["reason"] == "unknown_vehicle" and p.get("plate")})
        message = _("toll_import.notify_message", lang, count=len(batch.problems), filename=batch.filename)
        if unknown:
            message += " " + _("toll_import.notify_unknown_plates", lang, plates=", ".join(unknown))
        self.session.add(
            Notification(
                user_id=user.id,
                title=_("toll_import.notify_title", lang),
                message=message,
                type=NotificationType.SYSTEM,
                entity_type="expense_import",
                entity_id=batch.id,
                title_key="toll_import.notify_title",
                message_key="toll_import.notify_message_plates" if unknown else "toll_import.notify_message",
                params={"count": len(batch.problems), "filename": batch.filename, "plates": ", ".join(unknown)},
            )
        )

    async def cancel(self, batch_id: UUID) -> None:
        """Discard a preview; an applied upload stays."""
        batch = await self.session.get(TollImportBatch, batch_id)
        if batch is not None and batch.status == TollImportStatus.PREVIEW:
            await self.session.delete(batch)
            await self.session.flush()
