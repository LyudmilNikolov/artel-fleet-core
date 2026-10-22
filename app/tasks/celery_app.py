from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery_app = Celery(
    "fleetcore",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks.reminders", "app.tasks.notifications"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        # Cheap tick: sends each rule's reminders once per local day, at its send time (see ReminderDispatcher)
        "send-due-reminders": {
            "task": "app.tasks.notifications.send_due_reminders",
            "schedule": crontab(minute="*/15"),
        },
        "check-maintenance-reminders": {
            "task": "app.tasks.reminders.check_maintenance_reminders",
            "schedule": crontab(hour=8, minute=0),
        },
        "check-service-due": {
            "task": "app.tasks.reminders.check_service_due",
            "schedule": crontab(hour=8, minute=0),
        },
        "check-compliance-expiry": {
            "task": "app.tasks.reminders.check_compliance_expiry",
            "schedule": crontab(hour=8, minute=0),
        },
        "check-driver-document-expiry": {
            "task": "app.tasks.reminders.check_driver_document_expiry",
            "schedule": crontab(hour=8, minute=0),
        },
    },
)
