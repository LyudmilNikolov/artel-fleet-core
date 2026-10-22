from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.status import HTTP_303_SEE_OTHER

from app.audit_context import set_audit_user
from app.database import get_db
from app.i18n.locales import resolve_locale
from app.models.user import User, UserRole
from app.repositories.user_repo import UserRepository
from app.services.driver_scope import UNRESTRICTED, DriverScope, driver_may_open, resolve_scope


class WebRedirectException(HTTPException):
    """Exception that triggers a redirect in web routes."""

    def __init__(self, url: str):
        super().__init__(status_code=HTTP_303_SEE_OTHER, headers={"Location": url})


async def get_web_user(request: Request, db: AsyncSession = Depends(get_db)) -> User | None:
    """Get current user from session cookie. Returns None if not authenticated."""
    user_id = request.session.get("user_id")
    if not user_id:
        resolve_locale(request)
        return None
    repo = UserRepository(db)
    user = await repo.get_by_id(UUID(user_id))
    if not user or not user.is_active:
        language = request.session.get("lang")
        request.session.clear()
        if language in ("bg", "en"):
            request.session["lang"] = language
        resolve_locale(request)
        return None
    resolve_locale(request, user)
    set_audit_user(user.id)
    scope = await resolve_scope(db, user)
    request.state.driver_scope = scope
    if scope.restricted and not driver_may_open(request.url.path, request.method, scope):
        if request.headers.get("HX-Request") == "true":
            raise HTTPException(status_code=403, detail="errors.forbidden")
        raise WebRedirectException(scope.home)  # a driver only has their profile and assigned vehicles
    return user


def driver_scope_of(request: Request) -> DriverScope:
    """Visibility of the logged-in user, set by ``get_web_user`` (unrestricted for everyone but drivers)."""
    return getattr(request.state, "driver_scope", UNRESTRICTED)


async def require_web_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    """Require authenticated user, redirect to login if not."""
    user = await get_web_user(request, db)
    if not user:
        raise WebRedirectException("/login")
    return user


def web_require_roles(*roles: UserRole):
    """Dependency factory for web routes requiring specific roles."""

    async def check_role(request: Request, db: AsyncSession = Depends(get_db)) -> User:
        user = await get_web_user(request, db)
        if not user:
            raise WebRedirectException("/login")
        if user.role not in roles:
            raise WebRedirectException("/")
        return user

    return check_role
