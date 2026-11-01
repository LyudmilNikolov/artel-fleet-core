"""Notification settings pages: reminder rules, global send settings and personal channels."""

import asyncio
import re
from datetime import time
from urllib.parse import quote, urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as app_settings
from app.database import get_db
from app.models.user import User, UserRole
from app.notification_rules import (
    RECIPIENT_EMAIL,
    RECIPIENT_ROLE,
    RECIPIENT_ROLES,
    RECIPIENT_TELEGRAM,
    RECIPIENT_USER,
    SOURCE_BY_CODE,
)
from app.services.errors import DomainError, render_error
from app.services.notification_rule_service import (
    EffectiveRule,
    NotificationRuleService,
    RecipientInput,
    RuleInput,
    preview_text,
)
from app.services.reminder_messages import build_test_message
from app.utils.email import email_sender
from app.utils.telegram import telegram_bot
from app.web.deps import web_require_roles

router = APIRouter(prefix="/settings/notifications", tags=["web-notification-settings"])

# Saving rules and the global settings are administrator-only. Fleet managers may read the rules (read-only
# page) and manage their own channels (docs/plans/notifications.md).
require_admin = web_require_roles(UserRole.ADMIN)
require_staff = web_require_roles(UserRole.ADMIN, UserRole.FLEET_MANAGER)

TIME_PATTERN = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
COMMON_TIMEZONES = ("Europe/Sofia", "UTC", "Europe/London", "Europe/Berlin", "Europe/Athens", "Europe/Istanbul")


# --- form parsing ----------------------------------------------------------------------------


def _int_or_none(value: str, error_key: str) -> int | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        raise DomainError(error_key, max=10, days=365) from None


def _parse_time(value: str, *, required: bool = False) -> time | None:
    value = (value or "").strip()
    if not value and not required:
        return None
    match = TIME_PATTERN.match(value)
    if not match:
        raise DomainError("error.notification_time_invalid")
    return time(int(match.group(1)), int(match.group(2)))


def _lines(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[\n,;]+", value or "") if part.strip()]


def _raw_rule_form(form) -> dict:
    """The submitted values as typed, so a rejected form can be shown again unchanged."""
    return {
        "enabled": bool(form.get("enabled")),
        "email_enabled": bool(form.get("email_enabled")),
        "telegram_enabled": bool(form.get("telegram_enabled")),
        "milestones": str(form.get("milestones") or ""),
        "repeat_every_days": str(form.get("repeat_every_days") or ""),
        "stop_after_overdue_days": str(form.get("stop_after_overdue_days") or ""),
        "send_time": str(form.get("send_time") or ""),
        "users": [str(v) for v in form.getlist("recipient_users")],
        "roles": [str(v) for v in form.getlist("recipient_roles")],
        "emails": str(form.get("recipient_emails") or ""),
        "telegram": str(form.get("recipient_telegram") or ""),
    }


def _rule_input(raw: dict) -> RuleInput:
    try:
        milestones = [int(part) for part in _lines(raw["milestones"])]
    except ValueError:
        raise DomainError("error.notification_milestones_invalid", max=10, days=365) from None
    recipients = []
    for value in raw["users"]:
        try:
            recipients.append(RecipientInput(RECIPIENT_USER, user_id=UUID(value)))
        except ValueError:
            raise DomainError("error.notification_recipient_invalid") from None
    recipients += [RecipientInput(RECIPIENT_ROLE, role=role) for role in raw["roles"]]
    recipients += [RecipientInput(RECIPIENT_EMAIL, address=a) for a in _lines(raw["emails"])]
    recipients += [RecipientInput(RECIPIENT_TELEGRAM, address=a) for a in _lines(raw["telegram"])]
    return RuleInput(
        enabled=raw["enabled"],
        email_enabled=raw["email_enabled"],
        telegram_enabled=raw["telegram_enabled"],
        milestone_days=milestones,
        repeat_every_days=_int_or_none(raw["repeat_every_days"], "error.notification_repeat_invalid"),
        stop_after_overdue_days=_int_or_none(raw["stop_after_overdue_days"], "error.notification_stop_after_invalid"),
        send_time=_parse_time(raw["send_time"]),
        recipients=recipients,
    )


# --- rules page ---------------------------------------------------------------------------------


def _view(rule: EffectiveRule, t, raw: dict | None = None) -> dict:
    """What one rule card shows: the stored rule, or the rejected submission when there is one."""
    source = SOURCE_BY_CODE[rule.source]
    if raw is None:
        raw = {
            "enabled": rule.enabled,
            "email_enabled": rule.email_enabled,
            "telegram_enabled": rule.telegram_enabled,
            "milestones": ", ".join(str(d) for d in rule.milestone_days),
            "repeat_every_days": "" if rule.repeat_every_days is None else str(rule.repeat_every_days),
            "stop_after_overdue_days": ""
            if rule.stop_after_overdue_days is None
            else str(rule.stop_after_overdue_days),
            "send_time": rule.send_time.strftime("%H:%M") if rule.send_time else "",
            "users": [str(r.user_id) for r in rule.recipients if r.kind == RECIPIENT_USER],
            "roles": [r.role for r in rule.recipients if r.kind == RECIPIENT_ROLE],
            "emails": "\n".join(r.address for r in rule.recipients if r.kind == RECIPIENT_EMAIL),
            "telegram": "\n".join(r.address for r in rule.recipients if r.kind == RECIPIENT_TELEGRAM),
        }
    return {
        "source": rule.source,
        "owner": source.owner,
        "anchor": "rule-" + rule.source.replace(":", "-"),
        "label_key": source.label_key,
        "configured": rule.configured,
        "warnings": rule.warnings,
        "recipient_labels": [r.label for r in rule.recipients],
        "recipient_count": len(rule.recipients),
        "state": "unset" if not rule.configured else "off" if not rule.enabled else "warning" if rule.warnings else "active",
        "preview": preview_text(t, rule.milestone_days, rule.repeat_every_days, rule.stop_after_overdue_days),
        "channels": [c for c, on in (("email", rule.email_enabled), ("telegram", rule.telegram_enabled)) if on],
        "enabled": rule.enabled,
        "form": raw,
    }


async def _pickable_users(db: AsyncSession) -> list[User]:
    rows = await db.scalars(
        select(User).where(User.is_active.is_(True), User.role != UserRole.DRIVER).order_by(User.full_name)
    )
    return list(rows.all())


async def _rules_page(
    request: Request,
    db: AsyncSession,
    user: User,
    *,
    error: str | None = None,
    error_source: str | None = None,
    raw: dict | None = None,
    status_code: int = 200,
):
    t = request.app.state.template_globals(request)["_"]
    rules = await NotificationRuleService(db).rules()
    views = [_view(r, t, raw if r.source == error_source else None) for r in rules]
    query = request.query_params
    notice = None
    if query.get("applied", "").isdigit():
        notice = t("notifications.apply_done", count=int(query["applied"]))
    elif query.get("saved"):
        notice = t("notifications.rule_saved")
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="notifications/rules.html",
        status_code=status_code,
        context={
            "request": request,
            "user": user,
            "active_page": "notification_rules",
            "can_edit": user.role == UserRole.ADMIN,
            "groups": [
                ("vehicle", [v for v in views if v["owner"] == "vehicle"]),
                ("driver", [v for v in views if v["owner"] == "driver"]),
            ],
            "users": await _pickable_users(db),
            "roles": RECIPIENT_ROLES,
            "error": error,
            "error_source": error_source,
            "notice": notice,
            "i18n": {
                "preview": t("notifications.preview", days="{days}"),
                "daily": t("notifications.preview_daily"),
                "every": t("notifications.preview_every", n="{n}"),
                "stop": t("notifications.preview_stop", n="{n}"),
            },
            **request.app.state.template_globals(request),
        },
    )


@router.get("")
async def notification_settings_home(user: User = Depends(require_staff)):
    return RedirectResponse(url="/settings/notifications/rules", status_code=302)


@router.get("/rules", response_class=HTMLResponse)
async def rules_page(request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_staff)):
    return await _rules_page(request, db, user)


@router.post("/rules/apply")
async def rules_apply(request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    form = await request.form()
    try:
        changed = await NotificationRuleService(db).apply_to_group(
            str(form.get("owner") or ""), str(form.get("from_source") or ""), user.id
        )
    except DomainError:
        return RedirectResponse(url="/settings/notifications/rules", status_code=303)
    return RedirectResponse(url=f"/settings/notifications/rules?applied={changed}", status_code=303)


@router.post("/rules/{source}")
async def rule_save(
    source: str, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)
):
    if source not in SOURCE_BY_CODE:
        return RedirectResponse(url="/settings/notifications/rules", status_code=303)
    raw = _raw_rule_form(await request.form())
    try:
        await NotificationRuleService(db).save_rule(source, _rule_input(raw), user.id)
    except DomainError as error:
        return await _rules_page(
            request,
            db,
            user,
            error=render_error(error, request.state.locale),
            error_source=source,
            raw=raw,
            status_code=422,
        )
    anchor = "rule-" + source.replace(":", "-")
    return RedirectResponse(
        url=f"/settings/notifications/rules?{urlencode({'saved': source})}#{quote(anchor)}", status_code=303
    )


# --- global settings (admin) ------------------------------------------------------------------------


async def _settings_page(request: Request, db: AsyncSession, user: User, *, error=None, form=None, status_code=200):
    current = await NotificationRuleService(db).settings()
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="notifications/settings.html",
        status_code=status_code,
        context={
            "request": request,
            "user": user,
            "active_page": "notification_settings",
            "form": form or {"send_time": current.send_time.strftime("%H:%M"), "timezone": current.timezone},
            "error": error,
            "notice": request.query_params.get("saved") and True,
            "timezones": COMMON_TIMEZONES,
            # Only whether a channel is configured is shown, never the credentials.
            "smtp_ok": email_sender.configured,
            "telegram_ok": bool(app_settings.TELEGRAM_BOT_TOKEN),
            **request.app.state.template_globals(request),
        },
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    return await _settings_page(request, db, user)


@router.post("/settings")
async def settings_save(request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    raw = await request.form()
    form = {"send_time": str(raw.get("send_time") or ""), "timezone": str(raw.get("timezone") or "")}
    try:
        await NotificationRuleService(db).update_settings(
            send_time=_parse_time(form["send_time"], required=True), timezone=form["timezone"], user_id=user.id
        )
    except DomainError as error:
        return await _settings_page(
            request, db, user, error=render_error(error, request.state.locale), form=form, status_code=422
        )
    return RedirectResponse(url="/settings/notifications/settings?saved=1", status_code=303)


# --- my channels ------------------------------------------------------------------------------------


async def _channels_page(request: Request, db: AsyncSession, user: User, *, error=None, form=None, status_code=200):
    current = await NotificationRuleService(db).channels(user.id)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="notifications/channels.html",
        status_code=status_code,
        context={
            "request": request,
            "user": user,
            "active_page": "notification_channels",
            "form": form
            or {
                "email_enabled": current.email_enabled,
                "telegram_enabled": current.telegram_enabled,
                "telegram_chat_id": current.telegram_chat_id or "",
            },
            "error": error,
            "notice": request.query_params.get("saved") and True,
            **_test_notice(request),
            **request.app.state.template_globals(request),
        },
    )


TEST_RESULTS = {
    "email_ok": ("notifications.test_email_ok", True),
    "telegram_ok": ("notifications.test_telegram_ok", True),
    "email_failed": ("notifications.test_failed", False),
    "telegram_failed": ("notifications.test_failed", False),
    "email_not_configured": ("notifications.test_not_configured", False),
    "telegram_not_configured": ("notifications.test_not_configured", False),
    "email_no_target": ("notifications.test_no_target", False),
    "telegram_no_target": ("notifications.test_no_target", False),
}


def _test_notice(request: Request) -> dict:
    """The message for ``?test=<channel>_<result>`` after a "send test" button."""
    entry = TEST_RESULTS.get(request.query_params.get("test", ""))
    if not entry:
        return {"test_notice": None, "test_ok": False}
    key, ok = entry
    return {"test_notice": request.app.state.template_globals(request)["_"](key), "test_ok": ok}


@router.post("/channels/test/{channel}")
async def channels_send_test(
    channel: str, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_staff)
):
    """Send a test message to my own email address or saved Telegram chat (proves the channel works)."""
    home = "/settings/notifications/channels"
    if channel not in ("email", "telegram"):
        return RedirectResponse(url=home, status_code=303)
    saved = await NotificationRuleService(db).channels(user.id)
    message = build_test_message(channel, user.language)
    if channel == "email":
        configured, target = email_sender.configured, user.email
    else:
        configured, target = bool(app_settings.TELEGRAM_BOT_TOKEN), saved.telegram_chat_id
    if not target:
        result = "no_target"
    elif not configured:
        result = "not_configured"
    else:
        try:
            if channel == "email":
                ok = await asyncio.to_thread(email_sender.send, target, message.subject, message.html, message.text)
            else:
                ok = await asyncio.to_thread(telegram_bot.send_message, target, message.telegram)
        except Exception:
            ok = False  # never show or log transport errors: they can contain credentials
        result = "ok" if ok else "failed"
    return RedirectResponse(url=f"{home}?test={channel}_{result}", status_code=303)


@router.get("/channels", response_class=HTMLResponse)
async def channels_page(request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_staff)):
    return await _channels_page(request, db, user)


@router.post("/channels")
async def channels_save(request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_staff)):
    raw = await request.form()
    form = {
        "email_enabled": bool(raw.get("email_enabled")),
        "telegram_enabled": bool(raw.get("telegram_enabled")),
        "telegram_chat_id": str(raw.get("telegram_chat_id") or ""),
    }
    try:
        await NotificationRuleService(db).save_channels(user.id, **form)
    except DomainError as error:
        return await _channels_page(
            request, db, user, error=render_error(error, request.state.locale), form=form, status_code=422
        )
    return RedirectResponse(url="/settings/notifications/channels?saved=1", status_code=303)
