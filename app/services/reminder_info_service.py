"""What the item page tells about reminders: when the next one goes out and what was already sent."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification_dispatch import NotificationDelivery, NotificationRun
from app.models.user import User
from app.services.attention_service import AttentionItem
from app.services.notification_rule_service import EffectiveRule
from app.services.reminder_schedule import latest_point, next_point


@dataclass(frozen=True)
class NextReminder:
    kind: str  # "today" | "date" | "none" | "stopped" | "off"
    day: date | None = None


@dataclass(frozen=True)
class HistoryRow:
    when: datetime
    channel: str
    recipient: str  # a user's name, or the address / chat id of an external recipient
    point: int
    status: str  # "sent" | "failed" | "pending"


class ReminderInfoService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def next_reminder(self, item: AttentionItem, rule: EffectiveRule, today: date) -> NextReminder:
        """When the next reminder for this document goes out, or why none will."""
        if item.stopped:
            return NextReminder("stopped")
        if not (rule.configured and rule.enabled and rule.has_channel and rule.recipients):
            return NextReminder("off")
        reached = latest_point(item.days_left, rule, item.first_seen_days)
        if reached is not None:
            already_sent = await self.session.scalar(
                select(func.count())
                .select_from(NotificationDelivery)
                .where(
                    NotificationDelivery.entity_type == item.entity_type,
                    NotificationDelivery.entity_id == item.entity_id,
                    NotificationDelivery.end_date == item.end_date,
                    NotificationDelivery.point == reached,
                    NotificationDelivery.status == "sent",
                )
            )
            if not already_sent:
                ran_today = await self.session.scalar(
                    select(func.count())
                    .select_from(NotificationRun)
                    .where(
                        NotificationRun.run_date == today,
                        NotificationRun.source == item.source,
                        NotificationRun.finished_at.is_not(None),
                    )
                )
                return NextReminder("date", today + timedelta(days=1)) if ran_today else NextReminder("today")
        upcoming = next_point(item.days_left, rule, item.first_seen_days)
        if upcoming is None:
            return NextReminder("none")
        return NextReminder("date", item.end_date - timedelta(days=upcoming))

    async def history(self, item: AttentionItem, limit: int = 50) -> list[HistoryRow]:
        """Sends for this document entry, newest first. Only codes and recipients, never document numbers."""
        rows = await self.session.execute(
            select(NotificationDelivery, User.full_name)
            .outerjoin(User, User.id == NotificationDelivery.user_id)
            .where(
                NotificationDelivery.entity_type == item.entity_type,
                NotificationDelivery.entity_id == item.entity_id,
            )
            .order_by(NotificationDelivery.updated_at.desc(), NotificationDelivery.id)
            .limit(limit)
        )
        return [
            HistoryRow(d.sent_at or d.updated_at, d.channel, name or d.recipient, d.point, d.status)
            for d, name in rows.all()
        ]
