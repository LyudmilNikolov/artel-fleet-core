"""Celery tasks for sending email and Telegram notifications."""

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.services.reminder_dispatcher import ReminderDispatcher
from app.tasks.celery_app import celery_app
from app.utils.email import email_sender
from app.utils.telegram import telegram_bot


@celery_app.task
def send_email_notification(to: str, subject: str, body_html: str):
    """Send an email notification asynchronously."""
    return email_sender.send(to, subject, body_html)


@celery_app.task
def send_telegram_notification(chat_id: str, text: str):
    """Send a Telegram notification asynchronously."""
    return telegram_bot.send_message(chat_id, text)


@celery_app.task(name="app.tasks.notifications.send_due_reminders")
def send_due_reminders() -> dict:
    """Beat tick (every 15 minutes): send each rule's document reminders once per local day, at its send time."""
    return asyncio.run(_send_due_reminders())


async def _send_due_reminders() -> dict:
    # A private engine per run: a task runs in its own event loop, so the web app's pooled engine cannot be shared.
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            dispatcher = ReminderDispatcher(
                session,
                send_email=lambda to, subject, html, text: email_sender.send(to, subject, html, text),
                send_telegram=lambda chat_id, text: telegram_bot.send_message(chat_id, text),
            )
            result = await dispatcher.run()
            await session.commit()
            return {"sources": result.sources, "planned": result.planned, "sent": result.sent, "failed": result.failed}
    finally:
        await engine.dispose()
