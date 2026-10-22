from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.models.user import UserRole
from app.utils.password_policy import MAX_LENGTH, MIN_LENGTH


class UserCreate(BaseModel):
    email: str = Field(..., max_length=255)
    username: str = Field(..., min_length=3, max_length=100)
    full_name: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=MIN_LENGTH, max_length=MAX_LENGTH)
    role: UserRole = UserRole.VIEWER
    language: Literal["bg", "en"] = "bg"
    driver_id: UUID | None = None  # required for role "driver": the driver profile this login belongs to


class UserUpdate(BaseModel):
    email: str | None = Field(None, max_length=255)
    full_name: str | None = Field(None, max_length=255)
    role: UserRole | None = None
    is_active: bool | None = None
    language: Literal["bg", "en"] | None = None
    driver_id: UUID | None = None  # driver profile to link (role "driver")

    @field_validator("language")
    @classmethod
    def language_must_be_supported(cls, value):
        if value is None:
            raise ValueError("validation.language")
        return value


class UserRead(BaseModel):
    id: UUID
    email: str
    username: str
    full_name: str
    role: UserRole
    is_active: bool
    language: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=MIN_LENGTH, max_length=MAX_LENGTH)
