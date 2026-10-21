from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.i18n.locales import SUPPORTED_LANGUAGES, normalize_language, resolve_locale
from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService
from app.services.errors import render_error
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


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    try:
        user, _, _ = await service.authenticate(username, password)
        request.session["user_id"] = str(user.id)
        language = request.session.get("lang")
        if language in SUPPORTED_LANGUAGES:
            await UserRepository(db).update(user, language=language)
        else:
            language = normalize_language(user.language)
        request.session["lang"] = language
        resolve_locale(request, user)
        return RedirectResponse(url="/", status_code=302)
    except ValueError:
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="auth/login.html",
            context={
                "request": request,
                "error": request.app.state.get_translation("auth.invalid_credentials", request),
                **request.app.state.template_globals(request),
            },
            status_code=401,
        )


@router.get("/logout")
async def logout(request: Request):
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
