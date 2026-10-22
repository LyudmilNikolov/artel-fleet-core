"""Automatic audit logging: every create / update / delete of an audited model is recorded.

Recorded inside the same transaction as the change (SQLAlchemy ``after_flush``), so an entry exists
exactly when the change is committed. Driver documents are excluded because their service writes
masked entries itself; notifications are system noise.
"""

import datetime as dt
import enum
import uuid
from decimal import Decimal

from sqlalchemy import event, insert, inspect
from sqlalchemy.orm import Session

from app.audit_context import current_audit_context
from app.models.audit_log import AuditAction, AuditLog

# table name -> entity_type shown in the audit log (labels: audit_entity.* in i18n)
AUDITED_TABLES = {
    "vehicles": "vehicle",
    "drivers": "driver",
    "maintenance_records": "maintenance",
    "expenses": "expense",
    "compliance_documents": "compliance_document",
    "documents": "document",
    "mileage_logs": "mileage_log",
    "users": "user",
}
SKIPPED_COLUMNS = {"created_at", "updated_at"}
SECRET_COLUMNS = {"hashed_password"}
MAX_TEXT = 500
_registered = False


def json_safe(value):
    """A JSON-storable form of a column value."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, enum.Enum):
        return json_safe(value.value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    text = str(value)
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "…"


def _label(obj) -> str | None:
    """Short human description of the record, kept inside the entry so deleted records stay readable."""
    table = obj.__table__.name
    try:
        if table == "vehicles":
            return f"{obj.license_plate} — {obj.brand} {obj.model}"
        if table == "drivers":
            return obj.full_name
        if table == "users":
            return f"{obj.username} ({obj.full_name})"
        if table == "maintenance_records":
            return obj.title
        if table == "expenses":
            return f"{json_safe(obj.category)} {json_safe(obj.amount)} EUR"
        if table == "compliance_documents":
            return str(json_safe(obj.document_type))
        if table == "documents":
            return obj.filename
        if table == "mileage_logs":
            return f"{obj.value} km"
    except Exception:  # noqa: BLE001 - a label must never break the business transaction
        return None
    return None


def _columns(obj):
    return [a for a in inspect(obj).mapper.column_attrs if a.key not in SKIPPED_COLUMNS]


def _snapshot(obj) -> dict:
    return {
        a.key: "***" if a.key in SECRET_COLUMNS else json_safe(getattr(obj, a.key, None))
        for a in _columns(obj)
        if getattr(obj, a.key, None) is not None
    }


def _diff(obj) -> dict:
    state = inspect(obj)
    changes = {}
    for attr in _columns(obj):
        history = state.attrs[attr.key].history
        if not history.has_changes():
            continue
        old = history.deleted[0] if history.deleted else None
        new = history.added[0] if history.added else None
        if json_safe(old) == json_safe(new):
            continue
        secret = attr.key in SECRET_COLUMNS
        changes[attr.key] = {"old": "***" if secret else json_safe(old), "new": "***" if secret else json_safe(new)}
    return changes


def _audited(obj) -> str | None:
    table = getattr(obj, "__table__", None)
    return AUDITED_TABLES.get(table.name) if table is not None else None


def build_entries(session: Session) -> list[dict]:
    """Audit rows for everything the session is about to write (called after flush, state still pre-flush)."""
    context = current_audit_context()
    entries = []

    def add(obj, action: AuditAction, changes: dict) -> None:
        label = _label(obj)
        entries.append(
            {
                "user_id": context.get("user_id"),
                "action": action,
                "entity_type": _audited(obj),
                "entity_id": getattr(obj, "id", None),
                "changes": ({"_label": label} if label else {}) | changes,
                "ip_address": context.get("ip_address"),
                "user_agent": context.get("user_agent"),
            }
        )

    for obj in session.new:
        if _audited(obj):
            add(obj, AuditAction.CREATE, _snapshot(obj))
    for obj in session.dirty:
        if _audited(obj) and session.is_modified(obj):
            diff = _diff(obj)
            if diff:
                add(obj, AuditAction.UPDATE, diff)
    for obj in session.deleted:
        if _audited(obj):
            add(obj, AuditAction.DELETE, _snapshot(obj))
    return entries


def _after_flush(session: Session, flush_context) -> None:
    entries = build_entries(session)
    if entries:
        session.connection().execute(insert(AuditLog.__table__), entries)


def register_audit_events() -> None:
    """Attach the audit listener to every SQLAlchemy session (idempotent)."""
    global _registered
    if not _registered:
        event.listen(Session, "after_flush", _after_flush)
        _registered = True
