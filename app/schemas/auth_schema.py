"""
SHA Fraud Detection — Auth Schemas

Covers: login, token responses, password change, token refresh.
"""

from typing import List

from pydantic import EmailStr, Field, field_validator

from app.schemas.base_schema import BaseSchema

# ── Requests ──────────────────────────────────────────────────────────────────


class LoginRequest(BaseSchema):
    """OAuth2 password flow login body."""

    email: EmailStr
    password: str = Field(min_length=8)


class RefreshTokenRequest(BaseSchema):
    """Body for POST /auth/refresh."""

    refresh_token: str


class PasswordChangeRequest(BaseSchema):
    """Body for PATCH /auth/password."""

    current_password: str
    new_password: str = Field(min_length=8)
    confirm_password: str

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v, info):
        if "new_password" in info.data and v != info.data["new_password"]:
            raise ValueError("Passwords do not match")
        return v


# ── Responses ─────────────────────────────────────────────────────────────────


class TokenResponse(BaseSchema):
    """Returned after successful login."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds until access token expires


class AccessTokenResponse(BaseSchema):
    """Returned after token refresh (access token only)."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int


class AuthUserResponse(BaseSchema):
    """
    Slim user object embedded in token responses.
    Shows only what the frontend needs immediately after login.
    """

    email: str
    full_name: str
    is_active: bool
    is_superuser: bool
    must_change_password: bool
    roles: List[str] = []


class LoginResponse(BaseSchema):
    """Full login response — tokens + user info."""

    tokens: TokenResponse
    user: AuthUserResponse


class LogoutResponse(BaseSchema):
    message: str = "Logged out successfully"
