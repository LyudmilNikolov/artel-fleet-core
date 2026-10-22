from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit_context import set_audit_user
from app.database import get_db
from app.i18n.locales import resolve_locale
from app.models.user import User, UserRole
from app.repositories.user_repo import UserRepository
from app.services.driver_scope import UNRESTRICTED, DriverScope, driver_may_call_api, resolve_scope
from app.utils.security import decode_token, password_fingerprint

security = HTTPBearer()


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Extract and validate user from JWT Bearer token."""
    payload = decode_token(credentials.credentials)
    if not payload or payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="auth.invalid_token")

    user_id = UUID(payload["sub"])
    repo = UserRepository(db)
    user = await repo.get_by_id(user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="auth.user_disabled")

    if payload.get("pv") != password_fingerprint(user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="auth.invalid_token")

    resolve_locale(request, user)
    set_audit_user(user.id)
    scope = await resolve_scope(db, user)
    request.state.driver_scope = scope
    if scope.restricted and not driver_may_call_api(request.url.path, request.method, scope):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="errors.forbidden")
    return user


def get_driver_scope(request: Request) -> DriverScope:
    """Visibility of the authenticated user (set by ``get_current_user``); unrestricted by default."""
    return getattr(request.state, "driver_scope", UNRESTRICTED)


def require_roles(*roles: UserRole):
    """Dependency factory: require user to have one of the specified roles."""

    async def check_role(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="errors.forbidden")
        return user

    return check_role


# Convenience shortcuts
require_admin = require_roles(UserRole.ADMIN)
require_fleet_manager = require_roles(UserRole.ADMIN, UserRole.FLEET_MANAGER)
require_driver = require_roles(UserRole.ADMIN, UserRole.FLEET_MANAGER, UserRole.DRIVER)
