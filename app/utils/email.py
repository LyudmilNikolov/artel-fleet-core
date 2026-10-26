"""Email sender: Brevo API or Gmail API over HTTPS when configured, SMTP otherwise.

Some hosts (Sevalla among them) block outbound SMTP ports, so production sends
through an HTTPS API on port 443. Local development can keep using SMTP.
"""

import base64
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
BREVO_SEND_URL = "https://api.brevo.com/v3/smtp/email"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
HTTP_TIMEOUT = 15
SMTP_SSL_PORTS = {465, 2465, 8465}


class EmailSender:
    """Sends email through the first configured transport: Brevo API, Gmail API, then SMTP."""

    def __init__(self, client: httpx.Client | None = None):
        self._client = client

    @property
    def uses_brevo_api(self) -> bool:
        """True when a Brevo API key is set."""
        return bool(settings.BREVO_API_KEY)

    @property
    def uses_gmail_api(self) -> bool:
        """True when all three Gmail API credentials are set."""
        return bool(settings.GMAIL_CLIENT_ID and settings.GMAIL_CLIENT_SECRET and settings.GMAIL_REFRESH_TOKEN)

    @property
    def configured(self) -> bool:
        """True when email can be sent by either transport."""
        return self.uses_brevo_api or self.uses_gmail_api or bool(settings.SMTP_HOST)

    @property
    def from_email(self) -> str:
        return settings.SMTP_FROM or settings.SMTP_USER

    def _build_message(self, to: str, subject: str, body_html: str, body_text: str | None) -> MIMEMultipart:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.from_email
        msg["To"] = to
        if body_text:
            msg.attach(MIMEText(body_text, "plain", "utf-8"))
        msg.attach(MIMEText(body_html, "html", "utf-8"))
        return msg

    def send(self, to: str, subject: str, body_html: str, body_text: str | None = None) -> bool:
        """Send an email. Returns True on success; failures are logged without credentials."""
        if not self.configured:
            logger.warning("Email not configured, skipping email to %s", to)
            return False
        try:
            if self.uses_brevo_api:
                self._send_brevo_api(to, subject, body_html, body_text)
            elif self.uses_gmail_api:
                self._send_gmail_api(self._build_message(to, subject, body_html, body_text))
            else:
                self._send_smtp(to, self._build_message(to, subject, body_html, body_text))
        except httpx.HTTPStatusError as exc:
            # Log only the status, never the request (it holds the API key or OAuth secrets).
            logger.error("Failed to send email to %s: email API returned %s", to, exc.response.status_code)
            return False
        except Exception as exc:
            logger.error("Failed to send email to %s: %s", to, type(exc).__name__)
            return False
        logger.info("Email sent to %s: %s", to, subject)
        return True

    def _post(self, url: str, **kwargs) -> httpx.Response:
        if self._client is not None:
            response = self._client.post(url, **kwargs)
        else:
            with httpx.Client(timeout=HTTP_TIMEOUT) as client:
                response = client.post(url, **kwargs)
        response.raise_for_status()
        return response

    def _send_brevo_api(self, to: str, subject: str, body_html: str, body_text: str | None) -> None:
        name, address = parseaddr(self.from_email)
        sender = {"email": address, "name": name} if name else {"email": address}
        payload = {"sender": sender, "to": [{"email": to}], "subject": subject, "htmlContent": body_html}
        if body_text:
            payload["textContent"] = body_text
        self._post(
            BREVO_SEND_URL,
            headers={"api-key": settings.BREVO_API_KEY, "accept": "application/json"},
            json=payload,
        )

    def _access_token(self) -> str:
        response = self._post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": settings.GMAIL_CLIENT_ID,
                "client_secret": settings.GMAIL_CLIENT_SECRET,
                "refresh_token": settings.GMAIL_REFRESH_TOKEN,
                "grant_type": "refresh_token",
            },
        )
        return response.json()["access_token"]

    def _send_gmail_api(self, msg: MIMEMultipart) -> None:
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        self._post(
            GMAIL_SEND_URL,
            headers={"Authorization": f"Bearer {self._access_token()}"},
            json={"raw": raw},
        )

    def _send_smtp(self, to: str, msg: MIMEMultipart) -> None:
        # Implicit-TLS ports connect encrypted; on any other port (587, or 8025 / 2525 / 80 at relays like
        # SMTP2GO, used where hosts block the standard ports) STARTTLS is required before logging in.
        if settings.SMTP_PORT in SMTP_SSL_PORTS:
            server_cm = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10)
        else:
            server_cm = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10)
        with server_cm as server:
            if settings.SMTP_PORT not in SMTP_SSL_PORTS:
                server.starttls()
            if settings.SMTP_USER and settings.SMTP_PASSWORD:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(self.from_email, [to], msg.as_string())


email_sender = EmailSender()
