from app.config import settings as s
from app.utils.email import email_sender
print(email_sender.send(s.SMTP_USER, "FleetCore test", "<p>It works</p>"))
