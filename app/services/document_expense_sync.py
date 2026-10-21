"""Keeps a Documents & Deadlines entry and its expense in step.

One cost, two views: a document with an amount is mirrored by a linked expense, and an expense in a
document category can carry the validity and create the linked document. The link lives on the
document (``compliance_documents.expense_id``). Amount and provider/vendor stay in sync both ways.

Financial records are never deleted implicitly: deleting a document keeps its expense, and clearing
a document's amount only unlinks the expense. Deleting an expense clears the cost on its document.
"""

from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.compliance import ComplianceDocument
from app.models.expense import Currency, Expense, ExpenseCategory
from app.repositories.compliance_repo import ComplianceRepository
from app.services.errors import DomainError
from app.vehicle_rules import ComplianceType, validate_compliance_type

DOCUMENT_CATEGORIES = frozenset(t.value for t in ComplianceType)


def is_document_category(category) -> bool:
    """True when an expense category mirrors a Documents & Deadlines type."""
    return str(getattr(category, "value", category)) in DOCUMENT_CATEGORIES


def expense_category_for(document_type: str) -> ExpenseCategory:
    """The expense category for a document type (same code); ``other`` if none exists yet."""
    try:
        return ExpenseCategory(document_type)
    except ValueError:
        return ExpenseCategory.OTHER


def _payment_date(start_date: date) -> date:
    """Expense date for a document's cost: the start date, but never in the future (already paid)."""
    return min(start_date, datetime.now(UTC).date())


async def sync_from_document(session: AsyncSession, doc: ComplianceDocument, user_id: UUID | None) -> None:
    """Mirror a document's amount and provider into its expense.

    No expense yet and an amount: create and link one. Linked and the amount was cleared: unlink and
    keep the expense. Linked otherwise: update the expense's amount and vendor.
    """
    if doc.expense_id is None:
        if doc.amount is None:
            return
        expense = Expense(
            vehicle_id=doc.vehicle_id,
            category=expense_category_for(doc.document_type),
            amount=doc.amount,
            currency=Currency.EUR,
            date=_payment_date(doc.start_date),
            vendor=doc.provider,
            description=doc.policy_number,
            created_by=user_id,
        )
        session.add(expense)
        await session.flush()
        doc.expense_id = expense.id
        await session.flush()
        return
    if doc.amount is None:
        doc.expense_id = None
        await session.flush()
        return
    expense = await session.get(Expense, doc.expense_id)
    if expense is not None:
        expense.amount = doc.amount
        expense.vendor = doc.provider
        await session.flush()


async def sync_from_expense(session: AsyncSession, expense: Expense) -> None:
    """Mirror an expense's amount and vendor into its linked document, if any."""
    doc = expense.compliance_document
    if doc is None:
        return
    doc.amount = expense.amount
    doc.provider = expense.vendor
    await session.flush()


async def release_document(session: AsyncSession, expense: Expense) -> None:
    """Before an expense is deleted: its document keeps existing but no longer has a cost."""
    doc = expense.compliance_document
    if doc is None:
        return
    doc.amount = None
    doc.expense_id = None
    await session.flush()


async def require_applicable(session: AsyncSession, vehicle_id: UUID, category) -> None:
    """Raise if the vehicle cannot hold a document of this category (locks the vehicle row)."""
    vehicle = await ComplianceRepository(session).lock_vehicle(vehicle_id)
    if vehicle is None:
        raise DomainError("error.compliance_vehicle_not_found")
    try:
        validate_compliance_type(vehicle.vehicle_type, str(getattr(category, "value", category)))
    except ValueError as exc:
        raise DomainError(str(exc)) from exc


async def create_document_for_expense(
    session: AsyncSession,
    expense: Expense,
    *,
    valid_from: date,
    valid_until: date,
    policy_number: str | None,
    user_id: UUID | None,
) -> ComplianceDocument:
    """Create the linked document for an expense in a document category."""
    doc = ComplianceDocument(
        vehicle_id=expense.vehicle_id,
        document_type=expense.category.value,
        start_date=valid_from,
        end_date=valid_until,
        amount=expense.amount,
        provider=expense.vendor,
        policy_number=policy_number,
        expense_id=expense.id,
        created_by=user_id,
    )
    session.add(doc)
    await session.flush()
    await session.refresh(expense, attribute_names=["compliance_document"])
    return doc
