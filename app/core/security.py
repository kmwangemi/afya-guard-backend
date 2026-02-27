"""
SHA Fraud Detection — Security Utilities
Handles:
  - Password hashing & verification (pwdlib)
  - JWT access & refresh token creation
  - Token decoding & validation
"""

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Optional

import jwt
from pwdlib import PasswordHash

from app.core.config import settings

password_hash = PasswordHash.recommended()

# ── Password Utils ────────────────────────────────────────────────────────────


def hash_password(plain_password: str) -> str:
    """Return a hashed plain-text password."""
    return password_hash.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain-text password against its hash."""
    return password_hash.verify(plain_password, hashed_password)


# ── Token Utils ───────────────────────────────────────────────────────────────


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a signed JWT access token.
    Payload should include at minimum: {"sub": str(user_id)}
    """
    to_encode = data.copy()
    expire = datetime.now(UTC) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(
        to_encode, settings.SECRET_KEY.get_secret_value(), algorithm=settings.ALGORITHM
    )


def create_refresh_token(data: dict) -> str:
    """
    Create a signed JWT refresh token (longer-lived).
    Stored hashed in DB to allow server-side revocation.
    """
    to_encode = data.copy()
    expire = datetime.now(UTC) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(
        to_encode, settings.SECRET_KEY.get_secret_value(), algorithm=settings.ALGORITHM
    )


def decode_token(token: str) -> dict:
    """
    Decode and validate a JWT token.
    Raises jwt.PyJWTError if invalid or expired.
    """
    return jwt.decode(
        token, settings.SECRET_KEY.get_secret_value(), algorithms=[settings.ALGORITHM]
    )


def hash_token(raw_token: str) -> str:
    """SHA-256 hash a raw refresh token for safe DB storage."""
    return hashlib.sha256(raw_token.encode()).hexdigest()


def verify_access_token(token: str) -> str | None:
    """Verifies the access token and returns the payload if valid."""
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY.get_secret_value(),
            algorithms=[settings.ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
    except jwt.InvalidTokenError:
        return None
    else:
        return payload.get("sub")
