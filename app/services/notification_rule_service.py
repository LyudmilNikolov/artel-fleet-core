"""Reminder configuration: per-source rules, global send settings and personal channels.

A source without a stored rule behaves like the code defaults in ``app/notification_rules.py`` (channels off until
configured), so reading never writes and new document types need no migration. Changes are validated here, written
under the audit log (rule and settings changes only, never document numbers) and returned as immutable views.
"""

import re
from dataclasses import dataclass, field
from datetime import time
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.audit_log import AuditAction
from app.models.notification import NotificationPreference
from app.models.notification_rule import NotificationRule, NotificationRuleRecipient, NotificationSettings
from app.models.user import User, UserRole
from app.notification_rules import (
    DEFAULT_MILESTONE_DAYS,
    DEFAULT_REPEAT_EVERY_DAYS,
    DEFAULT_SEND_TIME,
    DEFAULT_TIMEZONE,
    MAX_DAYS,
    MAX_MILESTONES,
    RECIPIENT_EMAIL,
    RECIPIENT_KINDS,
    RECIPIENT_ROLE,
    RECIPIENT_ROLES,
    RECIPIENT_TELEGRAM,
    RECIPIENT_USER,
    SOURCE_BY_CODE,
    SOURCES,
)
from app.services.audit_service import AuditService
from app.services.errors import DomainError
from app.services.user_service import valid_email

TELEGRAM_TARGET = re.compile(r"^(-?\d{5,20}|@[a-z][a-z0-9_]{4,31})$")
_KIND_ORDER = {kind: n for n, kind in enumerate(RECIPIENT_KINDS)}


@dataclass(frozen=True)
class RecipientInput:
    kind: str
    user_id: UUID | None = None
    role: str | None = None
    address: str | None = None


@dataclass(frozen=True)
class RuleInput:
    enabled: bool
    email_enabled: bool
    telegram_enabled: bool
    milestone_days: list[int]
    repeat_every_days: int | None
    stop_after_overdue_days: int | None
    send_time: time | None
    recipients: list[RecipientInput] = field(default_factory=list)


@dataclass(frozen=True)
class EffectiveRecipient:
    kind: str
    user_id: UUID | None = None
    role: str | None = None
    address: str | None = None
    label: str = ""  # a user's name, or the role / address


@dataclass(frozen=True)
class EffectiveRule:
    source: str
    enabled: bool
    email_enabled: bool
    telegram_enabled: bool
    milestone_days: tuple[int, ...]
    repeat_every_days: int | None
    stop_after_overdue_days: int | None
    send_time: time | None
    recipients: tuple[EffectiveRecipient, ...]
    configured: bool  # False = code defaults, nothing stored

    @property
    def has_channel(self) -> bool:
        return self.email_enabled or self.telegram_enabled

    @property
    def warnings(self) -> tuple[str, ...]:
        """Why a stored, enabled rule would send nothing (message keys)."""
        if not (self.configured and self.enabled):
            return ()
        if not self.has_channel:
            return ("notifications.warn_no_channel",)
        if not self.recipients:
            return ("notifications.warn_no_recipients",)
        return ()


@dataclass(frozen=True)
class EffectiveSettings:
    send_time: time
    timezone: str
    configured: bool


@dataclass(frozen=True)
class EffectiveChannels:
    email_enabled: bool
    telegram_enabled: bool
    telegram_chat_id: str | None


def preview_parts(milestones: tuple[int, ...] | list[int], repeat: int | None, stop_after: int | None) -> dict:
    """The pieces of the schedule sentence ("30, 15, 10 days before expiry, then every day ...")."""
    return {"days": ", ".join(str(d) for d in milestones), "repeat": repeat, "stop_after": stop_after}


def preview_text(t, milestones, repeat: int | None, stop_after: int | None) -> str:
    """The schedule as a sentence, for example "30, 15, 10 days before expiry, then every day until renewed".

    ``t`` translates a key with keyword arguments (the template ``_`` function).
    """
    parts = preview_parts(milestones, repeat, stop_after)
    pieces = [t("notifications.preview", days=parts["days"])]
    if repeat:
        pieces.append(t("notifications.preview_daily") if repeat == 1 else t("notifications.preview_every", n=repeat))
    if stop_after:
        pieces.append(t("notifications.preview_stop", n=stop_after))
    return ", ".join(pieces)


def _hhmm(value: time | None) -> str | None:
    return value.strftime("%H:%M") if value else None


def default_rule(source: str) -> EffectiveRule:
    return EffectiveRule(
        source=source,
        enabled=True,
        email_enabled=False,
        telegram_enabled=False,
        milestone_days=DEFAULT_MILESTONE_DAYS,
        repeat_every_days=DEFAULT_REPEAT_EVERY_DAYS,
        stop_after_overdue_days=None,
        send_time=None,
        recipients=(),
        configured=False,
    )


def _recipient_text(recipient: EffectiveRecipient | RecipientInput) -> str:
    value = getattr(recipient, "label", "") or recipient.address or recipient.role or str(recipient.user_id)
    return f"{recipient.kind}:{value}"


def _snapshot(rule: EffectiveRule) -> dict:
    """The audited view of a rule: plain JSON values, recipients as readable text."""
    return {
        "enabled": rule.enabled,
        "email_enabled": rule.email_enabled,
        "telegram_enabled": rule.telegram_enabled,
        "milestone_days": list(rule.milestone_days),
        "repeat_every_days": rule.repeat_every_days,
        "stop_after_overdue_days": rule.stop_after_overdue_days,
        "send_time": _hhmm(rule.send_time),
        "recipients": sorted(_recipient_text(r) for r in rule.recipients),
    }


def _diff(old: dict, new: dict) -> dict:
    return {key: {"old": old[key], "new": new[key]} for key in new if old[key] != new[key]}


class NotificationRuleService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.audit = AuditService(session)

    # --- rules -------------------------------------------------------------------------------

    async def _rows(self) -> dict[str, NotificationRule]:
        result = await self.session.scalars(select(NotificationRule).options(selectinload(NotificationRule.recipients)))
        return {row.source: row for row in result.all()}

    async def _labels(self, rows: list[NotificationRule]) -> dict[UUID, str]:
        ids = {r.user_id for row in rows for r in row.recipients if r.user_id}
        if not ids:
            return {}
        users = await self.session.scalars(select(User).where(User.id.in_(ids)))
        return {user.id: user.full_name for user in users.all()}

    def _effective(self, row: NotificationRule, labels: dict[UUID, str]) -> EffectiveRule:
        recipients = sorted(
            (
                EffectiveRecipient(
                    kind=r.kind,
                    user_id=r.user_id,
                    role=r.role,
                    address=r.address,
                    label=labels.get(r.user_id, "") if r.user_id else (r.role or r.address or ""),
                )
                for r in row.recipients
            ),
            key=lambda r: (_KIND_ORDER.get(r.kind, 99), r.label.casefold()),
        )
        return EffectiveRule(
            source=row.source,
            enabled=row.enabled,
            email_enabled=row.email_enabled,
            telegram_enabled=row.telegram_enabled,
            milestone_days=tuple(sorted(row.milestone_days, reverse=True)),
            repeat_every_days=row.repeat_every_days,
            stop_after_overdue_days=row.stop_after_overdue_days,
            send_time=row.send_time,
            recipients=tuple(recipients),
            configured=True,
        )

    async def rules(self) -> list[EffectiveRule]:
        """Every source in display order, stored rule or code defaults."""
        rows = await self._rows()
        labels = await self._labels(list(rows.values()))
        return [self._effective(rows[s.code], labels) if s.code in rows else default_rule(s.code) for s in SOURCES]

    async def rule(self, source: str) -> EffectiveRule:
        if source not in SOURCE_BY_CODE:
            raise DomainError("error.notification_unknown_source")
        row = (await self._rows()).get(source)
        if row is None:
            return default_rule(source)
        return self._effective(row, await self._labels([row]))

    async def _validate(self, data: RuleInput) -> tuple[list[int], list[RecipientInput]]:
        milestones = list(data.milestone_days)
        if (
            not 1 <= len(milestones) <= MAX_MILESTONES
            or len(set(milestones)) != len(milestones)
            or any(not isinstance(d, int) or isinstance(d, bool) or not 1 <= d <= MAX_DAYS for d in milestones)
        ):
            raise DomainError("error.notification_milestones_invalid", max=MAX_MILESTONES, days=MAX_DAYS)
        if data.repeat_every_days is not None and not 1 <= data.repeat_every_days <= MAX_DAYS:
            raise DomainError("error.notification_repeat_invalid", days=MAX_DAYS)
        if data.stop_after_overdue_days is not None and not 1 <= data.stop_after_overdue_days <= MAX_DAYS:
            raise DomainError("error.notification_stop_after_invalid", days=MAX_DAYS)
        return sorted(milestones, reverse=True), await self._validate_recipients(data.recipients)

    async def _validate_recipients(self, recipients: list[RecipientInput]) -> list[RecipientInput]:
        clean: dict[tuple, RecipientInput] = {}
        user_ids = {r.user_id for r in recipients if r.kind == RECIPIENT_USER and r.user_id}
        allowed: set[UUID] = set()
        if user_ids:
            rows = await self.session.scalars(
                select(User.id).where(User.id.in_(user_ids), User.is_active.is_(True), User.role != UserRole.DRIVER)
            )
            allowed = set(rows.all())
        for r in recipients:
            if r.kind == RECIPIENT_USER and r.user_id in allowed:
                clean[(r.kind, r.user_id)] = RecipientInput(r.kind, user_id=r.user_id)
            elif r.kind == RECIPIENT_ROLE and r.role in RECIPIENT_ROLES:
                clean[(r.kind, r.role)] = RecipientInput(r.kind, role=r.role)
            elif r.kind == RECIPIENT_EMAIL and valid_email((r.address or "").strip()):
                address = r.address.strip().lower()
                clean[(r.kind, address)] = RecipientInput(r.kind, address=address)
            elif r.kind == RECIPIENT_TELEGRAM and TELEGRAM_TARGET.match((r.address or "").strip().lower()):
                address = r.address.strip().lower()
                clean[(r.kind, address)] = RecipientInput(r.kind, address=address)
            else:
                raise DomainError("error.notification_recipient_invalid")
        return list(clean.values())

    async def save_rule(self, source: str, data: RuleInput, user_id: UUID | None) -> EffectiveRule:
        """Create or replace the rule of one source; audited with a diff when something changed."""
        return (await self._save(source, data, user_id))[0]

    async def _save(self, source: str, data: RuleInput, user_id: UUID | None) -> tuple[EffectiveRule, bool]:
        if source not in SOURCE_BY_CODE:
            raise DomainError("error.notification_unknown_source")
        milestones, recipients = await self._validate(data)
        row = (await self._rows()).get(source)
        before = _snapshot(self._effective(row, await self._labels([row]))) if row else _snapshot(default_rule(source))
        created = row is None
        if created:
            row = NotificationRule(source=source, milestone_days=milestones)
            row.recipients = []
            self.session.add(row)
        row.enabled, row.email_enabled, row.telegram_enabled = data.enabled, data.email_enabled, data.telegram_enabled
        row.milestone_days = milestones
        row.repeat_every_days = data.repeat_every_days
        row.stop_after_overdue_days = data.stop_after_overdue_days
        row.send_time = data.send_time
        row.updated_by = user_id
        self._sync_recipients(row, recipients)
        await self.session.flush()
        effective = self._effective(row, await self._labels([row]))
        after = _snapshot(effective)
        changes = {k: {"old": None, "new": v} for k, v in after.items()} if created else _diff(before, after)
        if changes:
            await self.audit.log(
                user_id=user_id,
                action=AuditAction.CREATE if created else AuditAction.UPDATE,
                entity_type="notification_rule",
                entity_id=row.id,
                changes={"_label": source, **changes},
            )
        return effective, created or bool(changes)

    def _sync_recipients(self, row: NotificationRule, wanted: list[RecipientInput]) -> None:
        """Keep matching recipients, drop removed ones, add new ones (avoids unique-index clashes on re-add)."""

        def key(r) -> tuple:
            return (r.kind, r.user_id or r.role or r.address)

        wanted_keys = {key(r): r for r in wanted}
        for existing in list(row.recipients):
            if key(existing) in wanted_keys:
                del wanted_keys[key(existing)]
            else:
                row.recipients.remove(existing)
        for r in wanted_keys.values():
            row.recipients.append(
                NotificationRuleRecipient(kind=r.kind, user_id=r.user_id, role=r.role, address=r.address)
            )

    async def apply_to_group(self, owner: str, from_source: str, user_id: UUID | None) -> int:
        """Copy one rule (schedule, channels, recipients) to every other source of an owner. Returns changed rules."""
        source = SOURCE_BY_CODE.get(from_source)
        if owner not in ("vehicle", "driver") or source is None or source.owner != owner:
            raise DomainError("errors.invalid_input")
        template = await self.rule(from_source)
        data = RuleInput(
            enabled=template.enabled,
            email_enabled=template.email_enabled,
            telegram_enabled=template.telegram_enabled,
            milestone_days=list(template.milestone_days),
            repeat_every_days=template.repeat_every_days,
            stop_after_overdue_days=template.stop_after_overdue_days,
            send_time=template.send_time,
            recipients=[RecipientInput(r.kind, r.user_id, r.role, r.address) for r in template.recipients],
        )
        changed = 0
        for target in SOURCES:
            if target.owner == owner and target.code != from_source:
                changed += (await self._save(target.code, data, user_id))[1]
        return changed

    # --- global settings ---------------------------------------------------------------------

    async def _settings_row(self) -> NotificationSettings | None:
        return await self.session.scalar(select(NotificationSettings))

    async def settings(self) -> EffectiveSettings:
        row = await self._settings_row()
        if row is None:
            return EffectiveSettings(DEFAULT_SEND_TIME, DEFAULT_TIMEZONE, False)
        return EffectiveSettings(row.send_time, row.timezone, True)

    async def update_settings(self, *, send_time: time, timezone: str, user_id: UUID | None) -> EffectiveSettings:
        """Default send time and time zone for reminders (audited)."""
        timezone = (timezone or "").strip()
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            raise DomainError("error.notification_timezone_invalid") from None
        send_time = send_time.replace(second=0, microsecond=0)
        before = await self.settings()
        row = await self._settings_row()
        if row is None:
            row = NotificationSettings()
            self.session.add(row)
        row.send_time, row.timezone, row.updated_by = send_time, timezone, user_id
        await self.session.flush()
        old = {"send_time": _hhmm(before.send_time), "timezone": before.timezone}
        changes = _diff(old, {"send_time": _hhmm(send_time), "timezone": timezone})
        if changes:
            await self.audit.log(
                user_id=user_id,
                action=AuditAction.UPDATE,
                entity_type="notification_settings",
                entity_id=row.id,
                changes={"_label": "notification_settings", **changes},
            )
        return EffectiveSettings(send_time, timezone, True)

    # --- personal channels ---------------------------------------------------------------------

    async def channels(self, user_id: UUID) -> EffectiveChannels:
        row = await self.session.scalar(select(NotificationPreference).where(NotificationPreference.user_id == user_id))
        if row is None:
            return EffectiveChannels(True, False, None)
        return EffectiveChannels(row.email_enabled, row.telegram_enabled, row.telegram_chat_id)

    async def save_channels(
        self, user_id: UUID, *, email_enabled: bool, telegram_enabled: bool, telegram_chat_id: str | None
    ) -> EffectiveChannels:
        """My email on/off and Telegram chat id. Telegram needs a chat id; a bad id is rejected."""
        chat_id = (telegram_chat_id or "").strip() or None
        if chat_id and not TELEGRAM_TARGET.match(chat_id.lower()):
            raise DomainError("error.notification_chat_id_invalid")
        if telegram_enabled and not chat_id:
            raise DomainError("error.notification_chat_id_required")
        row = await self.session.scalar(select(NotificationPreference).where(NotificationPreference.user_id == user_id))
        if row is None:
            row = NotificationPreference(user_id=user_id)
            self.session.add(row)
        row.email_enabled, row.telegram_enabled, row.telegram_chat_id = email_enabled, telegram_enabled, chat_id
        await self.session.flush()
        return EffectiveChannels(email_enabled, telegram_enabled, chat_id)
