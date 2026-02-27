"""
SHA Fraud Detection — Auth Routes

POST /api/v1/auth/login
POST /api/v1/auth/refresh
POST /api/v1/auth/logout
PATCH /api/v1/auth/password
"""

from fastapi import APIRouter, Depends, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser, get_db
from app.schemas.auth_schema import (
    AccessTokenResponse,
    LoginResponse,
    LogoutResponse,
    PasswordChangeRequest,
    RefreshTokenRequest,
)
from app.schemas.base_schema import MessageResponse
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/login", response_model=LoginResponse, summary="Login with email + password"
)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Authenticate with email (username field) + password.
    Returns JWT access token + refresh token.
    """
    ip = request.client.host if request and request.client else None
    return await AuthService.login(
        db, form_data.username, form_data.password, ip_address=ip
    )


@router.post(
    "/refresh", response_model=AccessTokenResponse, summary="Refresh access token"
)
async def refresh_token(
    body: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db),
):
    """Exchange a valid refresh token for a new access token."""
    return await AuthService.refresh(db, body.refresh_token)


@router.post(
    "/logout", response_model=LogoutResponse, summary="Logout — revoke refresh token"
)
async def logout(
    body: RefreshTokenRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Revoke the provided refresh token (server-side logout)."""
    return await AuthService.logout(db, body.refresh_token, current_user.id)


@router.patch(
    "/password", response_model=MessageResponse, summary="Change own password"
)
async def change_password(
    body: PasswordChangeRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Logged-in user changes their own password."""
    await AuthService.change_password(
        db, current_user, body.current_password, body.new_password
    )
    return MessageResponse(message="Password changed successfully")
