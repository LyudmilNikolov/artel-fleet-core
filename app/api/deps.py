from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit_context import set_audit_user
from app.database import get_db
from app.i18n.locales import resolve_locale
from app.models.user import User, UserRole
from app.repositories.user_repo import UserRepository
from app.utils.security import decode_token

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

    resolve_locale(request, user)
    set_audit_user(user.id)
    return user


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
