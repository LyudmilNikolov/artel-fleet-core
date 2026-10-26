"""Email transport choice: Gmail API over HTTPS when configured (SMTP ports are blocked on Sevalla), else SMTP."""

import base64
import email
import json
import logging

import httpx
import pytest

from app.config import settings
from app.utils import email as email_module
from app.utils.email import BREVO_SEND_URL, GMAIL_SEND_URL, GOOGLE_TOKEN_URL, EmailSender


@pytest.fixture(autouse=True)
def no_brevo(monkeypatch):
    monkeypatch.setattr(settings, "BREVO_API_KEY", "")


@pytest.fixture
def gmail_settings(monkeypatch):
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setattr(settings, "SMTP_FROM", "fleet@gmail.com")
    monkeypatch.setattr(settings, "GMAIL_CLIENT_ID", "client-id")
    monkeypatch.setattr(settings, "GMAIL_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(settings, "GMAIL_REFRESH_TOKEN", "refresh-token")


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_gmail_api_sends_over_https_and_never_opens_smtp(gmail_settings, monkeypatch):
    monkeypatch.setattr(email_module.smtplib, "SMTP", lambda *a, **k: pytest.fail("SMTP must not be used"))
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if str(request.url) == GOOGLE_TOKEN_URL:
            return httpx.Response(200, json={"access_token": "access-1"})
        return httpx.Response(200, json={"id": "msg-1"})

    assert EmailSender(_client(handler)).send("to@example.com", "Здравей", "<p>hi</p>", "hi") is True

    token_req, send_req = calls
    assert b"refresh_token=refresh-token" in token_req.content
    assert str(send_req.url) == GMAIL_SEND_URL
    assert send_req.headers["Authorization"] == "Bearer access-1"
    raw = json.loads(send_req.content)["raw"]
    message = email.message_from_bytes(base64.urlsafe_b64decode(raw))
    assert message["To"] == "to@example.com" and message["From"] == "fleet@gmail.com"


def test_gmail_api_failure_returns_false_and_logs_no_secrets(gmail_settings, caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    with caplog.at_level(logging.INFO):
        assert EmailSender(_client(handler)).send("to@example.com", "s", "<p>b</p>") is False
    assert "400" in caplog.text
    assert "client-secret" not in caplog.text and "refresh-token" not in caplog.text


def test_brevo_api_wins_and_sends_over_https(gmail_settings, monkeypatch):
    monkeypatch.setattr(settings, "BREVO_API_KEY", "brevo-key")
    monkeypatch.setattr(settings, "SMTP_FROM", "FleetCore <fleet@gmail.com>")
    monkeypatch.setattr(email_module.smtplib, "SMTP", lambda *a, **k: pytest.fail("SMTP must not be used"))
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(201, json={"messageId": "<m1>"})

    assert EmailSender(_client(handler)).send("to@example.com", "Здравей", "<p>hi</p>", "hi") is True
    (request,) = calls  # no Google token call: Brevo is used
    assert str(request.url) == BREVO_SEND_URL and request.headers["api-key"] == "brevo-key"
    assert json.loads(request.content) == {
        "sender": {"email": "fleet@gmail.com", "name": "FleetCore"},
        "to": [{"email": "to@example.com"}],
        "subject": "Здравей",
        "htmlContent": "<p>hi</p>",
        "textContent": "hi",
    }


def test_brevo_failure_returns_false_and_logs_no_key(monkeypatch, caplog):
    monkeypatch.setattr(settings, "BREVO_API_KEY", "brevo-key")
    with caplog.at_level(logging.INFO):
        sender = EmailSender(_client(lambda request: httpx.Response(401, json={"code": "unauthorized"})))
        assert sender.send("to@example.com", "s", "<p>b</p>") is False
    assert "401" in caplog.text and "brevo-key" not in caplog.text


def test_configured_by_either_transport(monkeypatch):
    for key in ("SMTP_HOST", "GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"):
        monkeypatch.setattr(settings, key, "")
    sender = EmailSender()
    assert not sender.configured
    assert sender.send("to@example.com", "s", "<p>b</p>") is False

    monkeypatch.setattr(settings, "GMAIL_CLIENT_ID", "id")
    monkeypatch.setattr(settings, "GMAIL_CLIENT_SECRET", "secret")
    assert not sender.configured  # a partial Gmail setup does not count
    monkeypatch.setattr(settings, "GMAIL_REFRESH_TOKEN", "token")
    assert sender.configured and sender.uses_gmail_api


def test_smtp_used_without_gmail_credentials(monkeypatch):
    for key in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"):
        monkeypatch.setattr(settings, key, "")
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "SMTP_PORT", 587)
    monkeypatch.setattr(settings, "SMTP_USER", "u")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "p")
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent.append((host, port))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            sent.append("tls")

        def login(self, user, password):
            sent.append("login")

        def sendmail(self, from_addr, to_addrs, body):
            sent.append(to_addrs)

    monkeypatch.setattr(email_module.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(email_module.smtplib, "SMTP_SSL", lambda *a, **k: pytest.fail("no implicit TLS on 587"))
    assert EmailSender().send("to@example.com", "s", "<p>b</p>") is True
    assert sent == [("smtp.example.com", 587), "tls", "login", ["to@example.com"]]

    # Sevalla blocks 25/465/587/2525: relays such as SMTP2GO on 8025 must still encrypt before login.
    sent.clear()
    monkeypatch.setattr(settings, "SMTP_PORT", 8025)
    assert EmailSender().send("to@example.com", "s", "<p>b</p>") is True
    assert sent == [("smtp.example.com", 8025), "tls", "login", ["to@example.com"]]

    # Implicit-TLS ports connect encrypted and skip STARTTLS.
    sent.clear()
    monkeypatch.setattr(settings, "SMTP_PORT", 8465)
    monkeypatch.setattr(email_module.smtplib, "SMTP", lambda *a, **k: pytest.fail("8465 is implicit TLS"))
    monkeypatch.setattr(email_module.smtplib, "SMTP_SSL", FakeSMTP)
    assert EmailSender().send("to@example.com", "s", "<p>b</p>") is True
    assert sent == [("smtp.example.com", 8465), "login", ["to@example.com"]]
