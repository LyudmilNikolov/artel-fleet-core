"""Sends the document reminders: one localized message per document, recipient and channel.

``run()`` is called by a cheap Celery tick every 15 minutes. For each enabled rule whose send time (the rule's own, or
the global one, in the configured time zone) has passed and which has not run yet for the local date, it

1. builds the attention items of that source, drops stopped ones, and for every resolved recipient and channel asks
   ``due_point`` which schedule point (if any) is due;
2. claims each delivery (a ``pending`` row under a unique key), sends it outside any open transaction, and records the
   result (``sent`` / ``failed``). A failed send is retried at the next run, a crashed run's stale claim is taken over;
3. records the finished run, so the rule is not processed again that day.

Because every send is claimed under a unique key, re-running (or two overlapping runs) never sends a point twice.
Channels that are not configured on the server are skipped without recording a failure; the latest unsent point
goes out once they are. Senders are injected, so tests use fakes.
"""

import asyncio
import logging
import time as time_module
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.notification import NotificationPreference
from app.models.notification_dispatch import NotificationDelivery, NotificationRun
from app.models.user import User, UserRole
from app.notification_rules import RECIPIENT_EMAIL, RECIPIENT_ROLE, RECIPIENT_TELEGRAM, RECIPIENT_USER
from app.services.attention_service import AttentionItem, AttentionService
from app.services.notification_rule_service import EffectiveRule, NotificationRuleService
from app.services.reminder_messages import build_message
from app.services.reminder_schedule import due_point
from app.services.validity import app_timezone, local_today
from app.utils.email import email_sender

logger = logging.getLogger(__name__)

CLAIM_TTL = text("interval '30 minutes'")  # a claim or run older than this is treated as crashed
TELEGRAM_PAUSE_SECONDS = 1.05  # Telegram allows about one message per second to the same chat
EMAIL = "email"
TELEGRAM = "telegram"
SUPPORTED_LANGUAGES = ("bg", "en")


@dataclass(frozen=True)
class Target:
    channel: str  # "email" | "telegram"
    address: str  # email address or Telegram chat id
    lang: str
    user_id: UUID | None = None


@dataclass(frozen=True)
class PlannedSend:
    item: AttentionItem
    target: Target
    point: int


@dataclass
class DispatchResult:
    sources: list[str] = field(default_factory=list)  # rules processed in this call
    planned: int = 0
    sent: int = 0
    failed: int = 0


class ReminderDispatcher:
    def __init__(
        self,
        session: AsyncSession,
        *,
        send_email: Callable[[str, str, str, str], bool],
        send_telegram: Callable[[str, str], bool],
        sleep: Callable[[float], None] = time_module.sleep,
        base_url: str | None = None,
        email_configured: Callable[[], bool] = lambda: email_sender.configured,
        telegram_configured: Callable[[], bool] = lambda: bool(settings.TELEGRAM_BOT_TOKEN),
    ):
        self.session = session
        self.send_email = send_email
        self.send_telegram = send_telegram
        self.sleep = sleep
        self.base_url = base_url or settings.APP_BASE_URL
        self.configured = {EMAIL: email_configured, TELEGRAM: telegram_configured}

    # --- one tick ----------------------------------------------------------------------------------

    async def run(self, now: datetime | None = None) -> DispatchResult:
        """Process every rule that is due. Safe to call at any time and as often as wanted."""
        now = (now or datetime.now(UTC)).astimezone(UTC)
        rules = NotificationRuleService(self.session)
        global_settings = await rules.settings()
        try:
            zone = ZoneInfo(global_settings.timezone)
        except Exception:
            zone = app_timezone()
        local_now = now.astimezone(zone)
        run_date, local_time = local_now.date(), local_now.time()

        due = [
            r
            for r in await rules.rules()
            if r.configured
            and r.enabled
            and r.has_channel
            and r.recipients
            and local_time >= (r.send_time or global_settings.send_time)
        ]
        result = DispatchResult()
        if not due:
            return result
        finished = set(
            await self.session.scalars(
                select(NotificationRun.source).where(
                    NotificationRun.run_date == run_date, NotificationRun.finished_at.is_not(None)
                )
            )
        )
        due = [r for r in due if r.source not in finished]
        if not due:
            return result

        items = await AttentionService(self.session, today=local_today(now)).items()
        by_source: dict[str, list[AttentionItem]] = defaultdict(list)
        for item in items:
            by_source[item.source].append(item)

        for rule in due:
            if not await self._start_run(run_date, rule.source):
                continue  # another worker is on it
            planned = await self._plan(rule, by_source.get(rule.source, []))
            await self.session.commit()  # no transaction stays open while messages go out
            sent, failed = await self._send_all(planned)
            await self.session.execute(
                update(NotificationRun)
                .where(NotificationRun.run_date == run_date, NotificationRun.source == rule.source)
                .values(finished_at=func.now(), planned=len(planned), sent=sent, failed=failed)
            )
            await self.session.commit()
            result.sources.append(rule.source)
            result.planned += len(planned)
            result.sent += sent
            result.failed += failed
        return result

    async def _start_run(self, run_date, source: str) -> bool:
        statement = (
            insert(NotificationRun)
            .values(run_date=run_date, source=source)
            .on_conflict_do_update(
                constraint="uq_notification_runs_day_source",
                set_={"started_at": func.now()},
                where=and_(NotificationRun.finished_at.is_(None), NotificationRun.started_at < func.now() - CLAIM_TTL),
            )
            .returning(NotificationRun.id)
        )
        claimed = (await self.session.execute(statement)).scalar() is not None
        await self.session.commit()
        return claimed

    # --- planning ---------------------------------------------------------------------------------------

    async def _targets(self, rule: EffectiveRule) -> list[Target]:
        """Resolved recipients of a rule for the channels it uses, de-duplicated by channel and address."""
        user_ids = {r.user_id for r in rule.recipients if r.kind == RECIPIENT_USER and r.user_id}
        roles = {r.role for r in rule.recipients if r.kind == RECIPIENT_ROLE and r.role}
        users: list[User] = []
        if user_ids or roles:
            conditions = []
            if user_ids:
                conditions.append(User.id.in_(user_ids))
            if roles:
                conditions.append(User.role.in_([UserRole(role) for role in roles]))
            rows = await self.session.scalars(
                select(User)
                .where(or_(*conditions), User.is_active.is_(True), User.role != UserRole.DRIVER)
                .order_by(User.full_name, User.id)
            )
            users = list(rows.all())
        prefs = {}
        if users:
            found = await self.session.scalars(
                select(NotificationPreference).where(NotificationPreference.user_id.in_([u.id for u in users]))
            )
            prefs = {p.user_id: p for p in found.all()}

        targets: dict[tuple[str, str], Target] = {}

        def add(channel: str, address: str | None, lang: str, user_id: UUID | None = None) -> None:
            address = (address or "").strip()
            if address and self.configured[channel]():
                targets.setdefault((channel, address.lower()), Target(channel, address, lang, user_id))

        for user in users:
            lang = user.language if user.language in SUPPORTED_LANGUAGES else settings.DEFAULT_LANGUAGE
            pref = prefs.get(user.id)
            if rule.email_enabled and (pref.email_enabled if pref else True):
                add(EMAIL, user.email, lang, user.id)
            if rule.telegram_enabled and pref and pref.telegram_enabled:
                add(TELEGRAM, pref.telegram_chat_id, lang, user.id)
        for recipient in rule.recipients:
            if recipient.kind == RECIPIENT_EMAIL and rule.email_enabled:
                add(EMAIL, recipient.address, settings.DEFAULT_LANGUAGE)
            elif recipient.kind == RECIPIENT_TELEGRAM and rule.telegram_enabled:
                add(TELEGRAM, recipient.address, settings.DEFAULT_LANGUAGE)
        return list(targets.values())

    async def _plan(self, rule: EffectiveRule, items: list[AttentionItem]) -> list[PlannedSend]:
        items = [item for item in items if not item.stopped]
        if not items:
            return []
        targets = await self._targets(rule)
        if not targets:
            return []
        occupied: dict[tuple, set[int]] = defaultdict(set)
        rows = await self.session.execute(
            select(
                NotificationDelivery.entity_id,
                NotificationDelivery.end_date,
                NotificationDelivery.channel,
                NotificationDelivery.recipient,
                NotificationDelivery.point,
            ).where(
                NotificationDelivery.entity_id.in_({item.entity_id for item in items}),
                or_(
                    NotificationDelivery.status == "sent",
                    and_(
                        NotificationDelivery.status == "pending",
                        NotificationDelivery.updated_at >= func.now() - CLAIM_TTL,
                    ),
                ),
            )
        )
        for entity_id, end_date, channel, recipient, point in rows.all():
            occupied[(entity_id, end_date, channel, recipient)].add(point)

        planned = []
        for item in items:
            for target in targets:
                sent = occupied[(item.entity_id, item.end_date, target.channel, target.address)]
                point = due_point(item.days_left, rule, sent, item.first_seen_days)
                if point is not None:
                    planned.append(PlannedSend(item, target, point))
        return planned

    # --- sending ----------------------------------------------------------------------------------------

    async def _claim(self, plan: PlannedSend) -> UUID | None:
        """Take the delivery for sending; None when another run has it or it was already sent."""
        item, target = plan.item, plan.target
        statement = (
            insert(NotificationDelivery)
            .values(
                entity_type=item.entity_type,
                entity_id=item.entity_id,
                end_date=item.end_date,
                channel=target.channel,
                recipient=target.address,
                user_id=target.user_id,
                point=plan.point,
                status="pending",
            )
            .on_conflict_do_update(
                constraint="uq_notification_deliveries_point",
                set_={"status": "pending", "error": None, "user_id": target.user_id, "updated_at": func.now()},
                where=or_(
                    NotificationDelivery.status == "failed",
                    and_(
                        NotificationDelivery.status == "pending",
                        NotificationDelivery.updated_at < func.now() - CLAIM_TTL,
                    ),
                ),
            )
            .returning(NotificationDelivery.id)
        )
        claimed = (await self.session.execute(statement)).scalar()
        await self.session.commit()
        return claimed

    async def _deliver(self, plan: PlannedSend) -> bool:
        message = build_message(plan.item, plan.target.lang, self.base_url)
        target = plan.target
        try:
            if target.channel == EMAIL:
                return bool(
                    await asyncio.to_thread(
                        self.send_email, target.address, message.subject, message.html, message.text
                    )
                )
            return bool(await asyncio.to_thread(self.send_telegram, target.address, message.telegram))
        except Exception:
            # Never log or store the exception text: transport errors can contain credentials.
            logger.error("Reminder send failed (%s)", target.channel)
            return False

    async def _send_all(self, planned: list[PlannedSend]) -> tuple[int, int]:
        sent = failed = 0
        telegram_chats: set[str] = set()
        for plan in planned:
            delivery_id = await self._claim(plan)
            if delivery_id is None:
                continue
            if plan.target.channel == TELEGRAM:
                if plan.target.address in telegram_chats:
                    await asyncio.to_thread(self.sleep, TELEGRAM_PAUSE_SECONDS)
                telegram_chats.add(plan.target.address)
            ok = await self._deliver(plan)
            await self.session.execute(
                update(NotificationDelivery)
                .where(NotificationDelivery.id == delivery_id)
                .values(
                    status="sent" if ok else "failed",
                    error=None if ok else "send_failed",
                    sent_at=func.now() if ok else None,
                    updated_at=func.now(),
                )
            )
            await self.session.commit()
            sent += ok
            failed += not ok
        return sent, failed
