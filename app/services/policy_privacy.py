"""Who may read vehicle policy / document numbers in full.

Viewers get the same masked form as driver document numbers (``•••• 1234``). Everybody else is unchanged.
The linked expense copies the policy number into its description, so that text is redacted as well.
"""

from app.driver_document_rules import mask_number
from app.models.user import User, UserRole


def can_see_policy_numbers(user: User) -> bool:
    """Viewers are read-only observers and never see full policy numbers."""
    return user.role != UserRole.VIEWER


def policy_number_for(user: User, value: str | None) -> str | None:
    """The policy number as ``user`` may see it."""
    return value if can_see_policy_numbers(user) else mask_number(value)


def expense_description_for(user: User, expense) -> str | None:
    """Expense description with the linked document's policy number masked for viewers."""
    description = expense.description
    document = expense.compliance_document
    if not description or can_see_policy_numbers(user) or document is None or not document.policy_number:
        return description
    return description.replace(document.policy_number, mask_number(document.policy_number))
