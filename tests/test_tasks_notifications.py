"""Celery wiring: the worker must know the tasks, and beat must tick every 15 minutes."""

from celery.schedules import crontab

from app.tasks.celery_app import celery_app


def test_worker_includes_the_task_modules():
    assert set(celery_app.conf.include) >= {"app.tasks.reminders", "app.tasks.notifications"}
    celery_app.loader.import_default_modules()
    for name in (
        "app.tasks.notifications.send_due_reminders",
        "app.tasks.reminders.check_compliance_expiry",
        "app.tasks.reminders.check_driver_document_expiry",
    ):
        assert name in celery_app.tasks, name


def test_beat_ticks_every_fifteen_minutes():
    entry = celery_app.conf.beat_schedule["send-due-reminders"]
    assert entry["task"] == "app.tasks.notifications.send_due_reminders"
    assert entry["schedule"] == crontab(minute="*/15")
