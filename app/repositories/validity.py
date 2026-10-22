"""SQL building blocks shared by compliance and driver document repositories."""

from datetime import date, timedelta

from sqlalchemy import Select, and_, select
from sqlalchemy.dialects.postgresql import distinct_on

STATUSES = ("valid", "expiring_soon", "expired")


def status_clause(column, status: str, today: date, window: int):
    """SQL condition for a computed status on an end-date column.

    ``no_expiry`` matches a NULL end date; the dated statuses never match NULL.
    """
    soon = today + timedelta(days=window)
    if status == "no_expiry":
        return column.is_(None)
    if status == "expired":
        return column < today
    if status == "expiring_soon":
        return and_(column >= today, column <= soon)
    if status == "valid":
        return column > soon
    raise ValueError("errors.invalid_input")


def current_ids(model, owner_column, *extra_where) -> Select:
    """IDs of the current entry per (owner, document_type).

    Latest end_date wins (a missing end date counts as the latest), then start_date, created_at, id.
    ``extra_where`` conditions filter candidates before the choice is made.
    """
    query = (
        select(model.id)
        .ext(distinct_on(owner_column, model.document_type))
        .order_by(
            owner_column,
            model.document_type,
            model.end_date.desc().nulls_first(),
            model.start_date.desc().nulls_last(),
            model.created_at.desc(),
            model.id.desc(),
        )
    )
    if extra_where:
        query = query.where(*extra_where)
    return query
