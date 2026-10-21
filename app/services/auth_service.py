from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.i18n.locales import SUPPORTED_LANGUAGES
from app.models.user import User, UserRole
from app.repositories.user_repo import UserRepository
from app.services.errors import DomainError
from app.utils.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


class AuthService:
    def __init__(self, session: AsyncSession):
        self.repo = UserRepository(session)
        self.session = session

    async def register(
        self,
        *,
        email: str,
        username: str,
        full_name: str,
        password: str,
        role: UserRole = UserRole.VIEWER,
        language: str = "bg",
    ) -> User:
        """Register a new user."""
        if language not in SUPPORTED_LANGUAGES:
            raise DomainError("validation.language")
        existing = await self.repo.get_by_username(username)
        if existing:
            raise DomainError("auth.username_taken")

        existing = await self.repo.get_by_email(email)
        if existing:
            raise DomainError("auth.email_registered")

        user = await self.repo.create(
            email=email,
            username=username,
            full_name=full_name,
            hashed_password=hash_password(password),
            role=role,
            language=language,
        )
        return user

    async def authenticate(self, username: str, password: str) -> tuple[User, str, str]:
        """Authenticate user, return (user, access_token, refresh_token)."""
        user = await self.repo.get_by_username(username)
        if not user or not verify_password(password, user.hashed_password):
            raise DomainError("auth.invalid_credentials")

        if not user.is_active:
            raise DomainError("auth.disabled")

        access_token = create_access_token(user.id, user.role.value)
        refresh_token = create_refresh_token(user.id)
        return user, access_token, refresh_token

    async def refresh_token(self, refresh_token: str) -> tuple[str, str]:
        """Refresh access token."""
        payload = decode_token(refresh_token)
        if not payload or payload.get("type") != "refresh":
            raise DomainError("auth.invalid_refresh")

        user_id = UUID(payload["sub"])
        user = await self.repo.get_by_id(user_id)
        if not user or not user.is_active:
            raise DomainError("auth.user_disabled")

        new_access = create_access_token(user.id, user.role.value)
        new_refresh = create_refresh_token(user.id)
        return new_access, new_refresh

    async def change_password(self, user_id: UUID, current_password: str, new_password: str) -> None:
        """Change user password."""
        user = await self.repo.get_by_id(user_id)
        if not user:
            raise DomainError("auth.user_missing")

        if not verify_password(current_password, user.hashed_password):
            raise DomainError("auth.current_password_incorrect")

        await self.repo.update(user, hashed_password=hash_password(new_password))

    async def get_user_by_id(self, user_id: UUID) -> User | None:
        """Get user by ID."""
        return await self.repo.get_by_id(user_id)
