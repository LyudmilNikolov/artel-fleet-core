"""Reminder messages: one short, localized message per document, always linking to its item page.

Document numbers are never included. Every dynamic value is escaped for HTML (email and Telegram).
"""

from dataclasses import dataclass
from html import escape

from app.i18n import _
from app.services.attention_service import AttentionItem
from app.utils.formatting import format_local_date


@dataclass(frozen=True)
class ReminderMessage:
    subject: str
    text: str  # plain text email body
    html: str  # HTML email body
    telegram: str  # Telegram HTML


def when_phrase(days_left: int, lang: str) -> str:
    """ "10 days left", "Expires today", "3 days overdue" ..."""
    if days_left == 0:
        return _("notifications.expires_today", lang)
    if days_left == 1:
        return _("notifications.one_day_left", lang)
    if days_left > 1:
        return _("notifications.days_left", lang, days=days_left)
    if days_left == -1:
        return _("notifications.one_day_overdue", lang)
    return _("notifications.overdue_days", lang, days=-days_left)


def item_link(item: AttentionItem, base_url: str) -> str:
    return base_url.rstrip("/") + item.url


def build_message(item: AttentionItem, lang: str, base_url: str) -> ReminderMessage:
    document = _(f"notification_source.{item.source}", lang)
    when = when_phrase(item.days_left, lang)
    link = item_link(item, base_url)
    title = _("reminder.title_expired" if item.status == "expired" else "reminder.title_expiring", lang)
    lines = [
        _("reminder.line_document", lang, document=document),
        _("reminder.line_subject", lang, subject=item.subject),
        _("reminder.line_valid_until", lang, date=format_local_date(item.end_date, lang)),
        _("reminder.line_when", lang, when=when),
    ]
    open_label = _("reminder.open", lang)
    hint = _("reminder.stop_hint", lang)
    subject = _("reminder.subject", lang, document=document, subject=item.subject, when=when)

    text = "\n".join([title, "", *lines, "", f"{open_label}: {link}", hint])
    items_html = "".join(f"<li>{escape(line)}</li>" for line in lines)
    html = (
        f"<h3>{escape(title)}</h3><ul>{items_html}</ul>"
        f'<p><a href="{escape(link, quote=True)}">{escape(open_label)}</a></p><p>{escape(hint)}</p>'
    )
    telegram = (
        f"<b>{escape(title)}</b>\n"
        + "\n".join(escape(line) for line in lines)
        + f'\n\n<a href="{escape(link, quote=True)}">{escape(open_label)}</a>'
    )
    return ReminderMessage(subject=subject, text=text, html=html, telegram=telegram)


def build_test_message(channel: str, lang: str) -> ReminderMessage:
    """The "send test" message: proves the channel works, carries no data."""
    subject = _("reminder.test_subject", lang)
    body = _("reminder.test_body", lang, channel=channel)
    return ReminderMessage(
        subject=subject,
        text=f"{subject}\n\n{body}",
        html=f"<h3>{escape(subject)}</h3><p>{escape(body)}</p>",
        telegram=f"<b>{escape(subject)}</b>\n{escape(body)}",
    )
