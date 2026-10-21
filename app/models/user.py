import enum

from sqlalchemy import Boolean, CheckConstraint, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, TimestampMixin, UUIDPrimaryKey


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    FLEET_MANAGER = "fleet_manager"
    DRIVER = "driver"
    VIEWER = "viewer"


class User(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("language IN ('bg', 'en')", name="users_language_supported"),)

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role"),
        nullable=False,
        default=UserRole.VIEWER,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    language: Mapped[str] = mapped_column(String(5), default="bg", server_default="bg", nullable=False)

    def __repr__(self) -> str:
        return f"<User {self.username} ({self.role.value})>"
