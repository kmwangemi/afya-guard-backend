"""
SHA Fraud Detection — Auth Service

Handles: login, token refresh, logout, password change.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Optional

import jwt
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.models.enums_model import AuditAction, TokenType
from app.models.refresh_token_model import RefreshToken
from app.models.user_model import User
from app.schemas.auth_schema import (
    AccessTokenResponse,
    AuthUserResponse,
    LoginResponse,
    LogoutResponse,
    TokenResponse,
)
from app.services.audit_service import AuditService


class AuthService:

    @staticmethod
    async def login(
        db: AsyncSession,
        email: str,
        password: str,
        ip_address: Optional[str] = None,
    ) -> LoginResponse:
        """
        Authenticate a user with email + password.
        Returns JWT access + refresh tokens on success.
        Increments failed_login_count on failure and locks account after 5 attempts.
        """
        # ✅ Eagerly load roles to avoid MissingGreenlet on user.roles access
        result = await db.execute(
            select(User)
            .options(selectinload(User.roles))
            .filter(User.email == email.lower())
        )
        user = result.scalars().first()
        # ── Account lockout check ──────────────────────────────────────────
        if user and user.locked_until and user.locked_until > datetime.now(UTC):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Account locked until {user.locked_until.isoformat()}. Contact admin.",
            )
        # ── Credential verification ────────────────────────────────────────
        if not user or not verify_password(password, user.hashed_password):
            if user:
                user.failed_login_count += 1
                if user.failed_login_count >= 5:
                    user.locked_until = datetime.now(UTC) + timedelta(minutes=30)
                await db.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is deactivated",
            )
        # ── Reset failed counter on success ────────────────────────────────
        user.failed_login_count = 0
        user.locked_until = None
        user.last_login_at = datetime.now(UTC)
        await db.commit()
        # ── Generate tokens ────────────────────────────────────────────────
        payload = {"sub": str(user.id)}
        access_token = create_access_token(payload)
        raw_refresh = create_refresh_token(payload)
        # Store hashed refresh token
        expires = datetime.now(UTC) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
        db_token = RefreshToken(
            user_id=user.id,
            token_hash=hash_token(raw_refresh),
            token_type=TokenType.REFRESH,
            expires_at=expires,
            ip_address=ip_address,
        )
        db.add(db_token)
        await db.commit()
        await AuditService.log(
            db,
            AuditAction.LOGIN,
            user_id=user.id,
            entity_type="User",
            entity_id=user.id,
            ip_address=ip_address,
        )
        return LoginResponse(
            tokens=TokenResponse(
                access_token=access_token,
                refresh_token=raw_refresh,
                expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            ),
            user=AuthUserResponse(
                id=user.id,
                email=user.email,
                full_name=user.full_name,
                roles=[r.name for r in user.roles],  # ✅ safe — eagerly loaded
                is_superuser=user.is_superuser,
                must_change_password=user.must_change_password,
            ),
        )

    @staticmethod
    async def refresh(
        db: AsyncSession,
        raw_refresh_token: str,
    ) -> AccessTokenResponse:
        """
        Exchange a valid refresh token for a new access token.
        The refresh token must exist in DB and not be revoked or expired.
        """
        try:
            payload = decode_token(raw_refresh_token)
            if payload.get("type") != "refresh":
                raise ValueError("Not a refresh token")
            user_id = uuid.UUID(payload["sub"])
        except (jwt.PyJWTError, ValueError, KeyError):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired refresh token",
            )
        token_hash = hash_token(raw_refresh_token)
        result = await db.execute(
            select(RefreshToken).filter(
                RefreshToken.token_hash == token_hash,
                RefreshToken.is_revoked == False,
                RefreshToken.expires_at > datetime.now(UTC),
            )
        )
        db_token = result.scalars().first()
        if not db_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token revoked or expired",
            )
        new_access = create_access_token({"sub": str(user_id)})
        return AccessTokenResponse(
            access_token=new_access,
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )

    @staticmethod
    async def logout(
        db: AsyncSession,
        raw_refresh_token: str,
        user_id: uuid.UUID,
    ) -> LogoutResponse:
        """Revoke the provided refresh token (server-side logout)."""
        token_hash = hash_token(raw_refresh_token)
        result = await db.execute(
            select(RefreshToken).filter(RefreshToken.token_hash == token_hash)
        )
        db_token = result.scalars().first()
        if db_token:
            db_token.is_revoked = True
            await db.commit()
        await AuditService.log(
            db,
            AuditAction.LOGOUT,
            user_id=user_id,
            entity_type="User",
            entity_id=user_id,
        )
        return LogoutResponse()

    @staticmethod
    async def change_password(
        db: AsyncSession,
        user: User,
        current_password: str,
        new_password: str,
    ) -> None:
        """Allow a logged-in user to change their own password."""
        if not verify_password(current_password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is incorrect",
            )
        user.hashed_password = hash_password(new_password)
        user.password_changed_at = datetime.now(UTC)
        user.must_change_password = False
        await db.commit()
        await AuditService.log(
            db,
            AuditAction.PASSWORD_CHANGED,
            user_id=user.id,
            entity_type="User",
            entity_id=user.id,
        )
