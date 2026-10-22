from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.audit_context import set_audit_user
from app.database import get_db
from app.models.audit_log import AuditAction
from app.models.user import User
from app.schemas.user import (
    LoginRequest,
    PasswordChange,
    RefreshRequest,
    TokenResponse,
    UserRead,
)
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.errors import error_detail
from app.utils import rate_limit
from app.utils.client_ip import request_ip
from app.utils.rate_limit import RateLimitedError

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    ip = request_ip(request)
    try:
        rate_limit.limiter.check(ip, data.username)
    except RateLimitedError as blocked:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"key": "auth.too_many_attempts", "params": {"minutes": -(-blocked.retry_after // 60)}},
            headers={"Retry-After": str(blocked.retry_after)},
        )
    service = AuthService(db)
    try:
        user, access_token, refresh_token = await service.authenticate(data.username, data.password)
        rate_limit.limiter.success(ip, data.username)
        set_audit_user(user.id)
        await AuditService(db).log(
            user_id=user.id,
            action=AuditAction.LOGIN,
            entity_type="session",
            entity_id=user.id,
            changes={"channel": "api"},
        )
        return TokenResponse(access_token=access_token, refresh_token=refresh_token)
    except ValueError as e:
        rate_limit.limiter.failure(ip, data.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=error_detail(e))


@router.post("/refresh", response_model=TokenResponse)
async def refresh(data: RefreshRequest, db: AsyncSession = Depends(get_db)):
    service = AuthService(db)
    try:
        access_token, refresh_token = await service.refresh_token(data.refresh_token)
        return TokenResponse(access_token=access_token, refresh_token=refresh_token)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=error_detail(e))


@router.get("/me", response_model=UserRead)
async def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    data: PasswordChange,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    try:
        await service.change_password(current_user.id, data.current_password, data.new_password)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_detail(e))
