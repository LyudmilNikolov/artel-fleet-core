from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.audit_context import AuditContextMiddleware
from app.config import settings
from app.i18n import _, get_available_languages, load_translations
from app.i18n.locales import resolve_locale
from app.security import GeoRestrictionMiddleware, SecurityHeadersMiddleware
from app.services.errors import render_error
from app.services.notification_text import notification_text
from app.utils.formatting import format_local_date, format_local_datetime, format_number, format_percent, money
from app.utils.rate_limit import configure_limiter
from app.web.csrf import get_token as get_csrf_token
from app.web.deps import WebRedirectException

TEMPLATES_DIR = Path(__file__).parent / "templates"


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_translations()
    yield


def create_app() -> FastAPI:
    application = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        docs_url="/docs" if settings.APP_DEBUG else None,
        openapi_url="/openapi.json" if settings.APP_DEBUG else None,
        redoc_url="/redoc" if settings.APP_DEBUG else None,
        lifespan=lifespan,
    )

    # Middleware
    application.add_middleware(
        SessionMiddleware,
        secret_key=settings.SECRET_KEY,
        session_cookie="fleetcore_session",
        max_age=12 * 3600,  # signed cookie, refreshed on every response: effectively a 12 hour idle timeout
        same_site="lax",
        https_only=settings.APP_ENV == "production",
    )

    application.add_middleware(AuditContextMiddleware)
    # Outermost last: headers wrap everything (also the 403 page), the country check runs before the app
    application.add_middleware(GeoRestrictionMiddleware)
    application.add_middleware(SecurityHeadersMiddleware)
    configure_limiter()

    # Templates
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    application.state.templates = templates

    def get_lang(request: Request) -> str:
        return getattr(request.state, "locale", None) or resolve_locale(request)

    def get_translation(key: str, request: Request, **kwargs) -> str:
        return _(key, lang=get_lang(request), **kwargs)

    application.state.get_translation = get_translation

    def template_globals(request: Request) -> dict:
        lang = get_lang(request)
        return {
            "lang": lang,
            "driver_scope": getattr(request.state, "driver_scope", None),
            "csrf_token": get_csrf_token(request),
            "fuel_average": lambda cost, liters: Decimal(str(cost)) / Decimal(str(liters)) if liters else None,
            "money": lambda value: money(value, lang),
            "notice_text": lambda notification: notification_text(notification, lang),
            "fmt_number": lambda value, decimals=0: format_number(value, lang, decimals),
            "fmt_percent": lambda value, decimals=1: format_percent(value, lang, decimals),
            "local_date": lambda value: format_local_date(value, lang),
            "local_dt": lambda value, seconds=False: format_local_datetime(value, lang, seconds),
            "languages": get_available_languages(),
            "_": lambda key, **kw: _(key, lang=lang, **kw),
        }

    application.state.template_globals = template_globals

    # Redirect exception handler (for web auth redirects)
    @application.exception_handler(WebRedirectException)
    async def redirect_exception_handler(request: Request, exc: WebRedirectException):
        return RedirectResponse(url=exc.headers["Location"], status_code=302)

    @application.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        lang = get_lang(request)
        errors = []
        for error in exc.errors():
            errors.append(
                {
                    "type": error["type"],
                    "loc": error["loc"],
                    "msg": _("validation.required" if error["type"] == "missing" else "validation.invalid", lang=lang),
                }
            )
        return JSONResponse(status_code=422, content={"detail": errors})

    @application.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException):
        detail = render_error(
            {401: "errors.unauthorized", 403: "errors.forbidden", 404: "errors.not_found"}.get(
                exc.status_code, exc.detail
            )
            if not isinstance(exc.detail, dict) and not str(exc.detail).startswith(("error.", "errors.", "auth."))
            else exc.detail,
            get_lang(request),
        )
        return JSONResponse(status_code=exc.status_code, content={"detail": detail}, headers=exc.headers)

    @application.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        """Browsers ask for /favicon.ico by default; serve the truck icon."""
        return FileResponse("static/img/favicon-32.png", media_type="image/png")

    # Static files
    application.mount("/static", StaticFiles(directory="static"), name="static")

    # API routes
    from app.api.v1.router import api_router

    application.include_router(api_router, prefix="/api/v1")

    # Web routes
    from app.web.router import web_router

    application.include_router(web_router)

    return application


app = create_app()
