"""
SHA Fraud Detection — FastAPI Dependencies

Provides reusable Depends() callables for:
  - Database session injection
  - Current user resolution from JWT
  - Permission-based access guards
  - Pagination parameter parsing
"""

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import verify_access_token
from app.models.user_model import User
from app.schemas.user_schema import UserResponse

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


# ── Auth ──────────────────────────────────────────────────────────────────────


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Get the currently authenticated user based on the provided JWT token."""
    user_id = verify_access_token(token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Validate user_id is a valid UUID (defence against malformed JWTs)
    try:
        user_uuid = uuid.UUID(user_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token format",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    # Fetch user from database
    result = await db.execute(select(User).filter(User.id == user_uuid))
    user = result.scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_current_active_superuser(
    current_user: User = Depends(get_current_user),
) -> User:
    """Allow only superusers through."""
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superuser privileges required",
        )
    return current_user


def require_permission(permission_name: str):
    """
    Dependency factory — guards a route behind a specific permission.

    Usage:
        @router.post("/cases")
        async def create_case(user = Depends(require_permission("create_case"))):
            ...
    """

    async def _check(current_user: User = Depends(get_current_user)) -> User:
        if not current_user.has_permission(permission_name):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission '{permission_name}' required",
            )
        return current_user

    return _check


# ── Pagination ────────────────────────────────────────────────────────────────


class PaginationParams:
    """Standard pagination query parameters injected via Depends()."""

    def __init__(
        self,
        page: int = 1,
        page_size: int = settings.DEFAULT_PAGE_SIZE,
    ):
        if page < 1:
            raise HTTPException(
                status_code=400,
                detail="page must be >= 1",
            )
        if page_size < 1 or page_size > settings.MAX_PAGE_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"page_size must be between 1 and {settings.MAX_PAGE_SIZE}",
            )
        self.page = page
        self.page_size = page_size
        self.offset = (page - 1) * page_size


# ── Convenience type aliases (defined after functions to avoid forward refs) ──

DBSession = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[UserResponse, Depends(get_current_user)]
