from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.user_model import UserRole


# =========================
# Base schema
# =========================
class UserBase(BaseModel):
    first_name: str = Field(..., json_schema_extra={"example": "John"})
    last_name: str = Field(..., json_schema_extra={"example": "Doe"})
    email: EmailStr = Field(..., json_schema_extra={"example": "john.doe@gmail.com"})
    phone_number: str = Field(..., json_schema_extra={"example": "+254712345678"})
    role: UserRole = Field(..., json_schema_extra={"example": "user"})

    @field_validator("first_name", "last_name", mode="before")
    @classmethod
    def normalize_names(cls, value: str) -> str:
        return value.strip().title() if isinstance(value, str) else value

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.lower().strip() if isinstance(value, str) else value


# =========================
# Create schema
# =========================
class UserCreate(UserBase):
    """
    Schema used when creating a user account.
    """

    password: str = Field(..., json_schema_extra={"example": "Test@123"})

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if len(value) < 6:
            raise ValueError("Password must be at least 6 characters long")
        if not any(char.isupper() for char in value):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(char in "!@#$%^&*()-_=+[]{}|;:'\",.<>?/`~" for char in value):
            raise ValueError("Password must contain at least one special character")
        return value


# =========================
# Update schema
# =========================
class UserUpdate(BaseModel):
    """
    Schema used for updating user profile details.
    """

    first_name: Optional[str] = Field(None, json_schema_extra={"example": "Jane"})
    last_name: Optional[str] = Field(None, json_schema_extra={"example": "Doe"})
    phone_number: Optional[str] = Field(
        None, json_schema_extra={"example": "+254798765432"}
    )
    profile_picture_url: Optional[str] = Field(
        None,
        json_schema_extra={"example": "https://cdn.example.com/profiles/user1.png"},
    )
    role: Optional[UserRole] = Field(None, json_schema_extra={"example": "admin"})

    @field_validator("first_name", "last_name", mode="before")
    @classmethod
    def normalize_names(cls, value: str) -> str:
        return value.strip().title() if isinstance(value, str) else value


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# =========================
# Response schema
# =========================
class UserResponse(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    profile_picture_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime
