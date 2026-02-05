from datetime import UTC, datetime, timedelta
from typing import Annotated, Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pwdlib import PasswordHash
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.config import settings
from app.core.database import get_db
from app.models.user_model import User
from app.schemas.user_schema import UserResponse

password_hash = PasswordHash.recommended()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/login")

# Database dependency
DbDependency = Annotated[AsyncSession, Depends(get_db)]
TokenDependency = Annotated[str, Depends(oauth2_scheme)]

# Token dependencies for each user type
UserTokenDependency = Depends(oauth2_scheme)


def hash_password(password: str) -> str:
    """Hashes a password."""
    return password_hash.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a password against a given hash."""
    return password_hash.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(
            minutes=settings.access_token_expire_minutes
        )
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(
        to_encode, settings.secret_key.get_secret_value(), algorithm=settings.algorithm
    )
    return encoded_jwt


def verify_access_token(token: str) -> str | None:
    """Verifies the access token and returns the payload if valid."""
    try:
        payload = jwt.decode(
            token,
            settings.secret_key.get_secret_value(),
            algorithms=[settings.algorithm],
            options={"require": ["exp", "sub"]},
        )
    except jwt.InvalidTokenError:
        return None
    else:
        return payload.get("sub")


async def get_current_user(token: TokenDependency, db: DbDependency):
    """Get the currently authenticated user based on the provided JWT token."""
    user_id = verify_access_token(token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # validate user_id is a valid integer (defence against malformed jwts)
    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    # Fetch user from database
    result = await db.execute(select(User).filter(User.id == user_id_int))
    user = result.scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


# Convenience type annotations for dependency injection
CurrentUser = Annotated[UserResponse, Depends(get_current_user)]
