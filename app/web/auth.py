from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit_context import set_audit_user
from app.database import get_db
from app.i18n.locales import SUPPORTED_LANGUAGES, normalize_language, resolve_locale
from app.models.audit_log import AuditAction
from app.repositories.user_repo import UserRepository
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.errors import render_error
from app.utils import rate_limit
from app.utils.client_ip import request_ip
from app.utils.rate_limit import RateLimitedError
from app.utils.security import password_fingerprint
from app.web.csrf import rotate_token
from app.web.deps import get_web_user

router = APIRouter(tags=["web-auth"])


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, user=Depends(get_web_user)):
    if user:
        return RedirectResponse(url="/", status_code=302)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="auth/login.html",
        context={"request": request, "error": None, **request.app.state.template_globals(request)},
    )


def _login_error(request: Request, message: str, status_code: int, headers: dict | None = None):
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="auth/login.html",
        context={"request": request, "error": message, **request.app.state.template_globals(request)},
        status_code=status_code,
        headers=headers,
    )


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    ip = request_ip(request)
    try:
        rate_limit.limiter.check(ip, username)
    except RateLimitedError as blocked:
        message = request.app.state.get_translation(
            "auth.too_many_attempts", request, minutes=-(-blocked.retry_after // 60)
        )
        return _login_error(request, message, 429, {"Retry-After": str(blocked.retry_after)})
    service = AuthService(db)
    try:
        user, _, _ = await service.authenticate(username, password)
    except ValueError:
        rate_limit.limiter.failure(ip, username)
        return _login_error(request, request.app.state.get_translation("auth.invalid_credentials", request), 401)
    rate_limit.limiter.success(ip, username)
    # A fresh session on every login (no session fixation), carrying only the language choice
    language = request.session.get("lang")
    request.session.clear()
    if language in SUPPORTED_LANGUAGES:
        await UserRepository(db).update(user, language=language)
    else:
        language = normalize_language(user.language)
    request.session["lang"] = language
    request.session["user_id"] = str(user.id)
    request.session["pv"] = password_fingerprint(user.hashed_password)
    rotate_token(request)
    resolve_locale(request, user)
    set_audit_user(user.id)
    await AuditService(db).log(
        user_id=user.id,
        action=AuditAction.LOGIN,
        entity_type="session",
        entity_id=user.id,
        changes={"channel": "web"},
    )
    return RedirectResponse(url="/", status_code=302)


@router.post("/logout")
async def logout(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if user:
        await AuditService(db).log(
            user_id=user.id,
            action=AuditAction.LOGOUT,
            entity_type="session",
            entity_id=user.id,
            changes={"channel": "web"},
        )
    language = request.session.get("lang")
    request.session.clear()
    if language in SUPPORTED_LANGUAGES:
        request.session["lang"] = language
    return RedirectResponse(url="/login", status_code=302)


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="auth/profile.html",
        context={
            "request": request,
            "user": user,
            "success": None,
            "error": None,
            **request.app.state.template_globals(request),
        },
    )


@router.post("/profile", response_class=HTMLResponse)
async def profile_submit(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    form = await request.form()
    action = form.get("action")
    success = None
    error = None

    if action == "update_profile":
        language = form.get("language", "bg")
        if language not in SUPPORTED_LANGUAGES:
            return request.app.state.templates.TemplateResponse(
                request=request,
                name="auth/profile.html",
                status_code=422,
                context={
                    "request": request,
                    "user": user,
                    "success": None,
                    "error": request.app.state.get_translation("validation.language", request),
                    **request.app.state.template_globals(request),
                },
            )
        repo = UserRepository(db)
        await repo.update(user, language=language)
        request.session["lang"] = language
        resolve_locale(request, user)
        success = request.app.state.get_translation("toast.saved", request)
    elif action == "change_password":
        current_password = form.get("current_password", "")
        new_password = form.get("new_password", "")
        service = AuthService(db)
        try:
            await service.change_password(user.id, current_password, new_password)
            await db.refresh(user)
            # this browser stays signed in; every other session and token of the user stops working
            request.session["pv"] = password_fingerprint(user.hashed_password)
            rotate_token(request)
            success = request.app.state.get_translation("toast.saved", request)
        except ValueError as e:
            error = render_error(e, request.state.locale)

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="auth/profile.html",
        context={
            "request": request,
            "user": user,
            "success": success,
            "error": error,
            **request.app.state.template_globals(request),
        },
    )


@router.get("/set-language/{lang_code}")
async def set_language(request: Request, lang_code: str, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if lang_code in SUPPORTED_LANGUAGES:
        request.session["lang"] = lang_code
        if user:
            await UserRepository(db).update(user, language=lang_code)
    next_url = request.query_params.get("next", "/")
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/"
    return RedirectResponse(url=next_url, status_code=302)
