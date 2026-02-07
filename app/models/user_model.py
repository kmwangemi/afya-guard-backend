from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import DateTime, Enum, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserRole(str, PyEnum):
    ADMIN = "admin"
    INVESTIGATOR = "investigator"
    ANALYST = "analyst"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        unique=True,
        nullable=False,
    )
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        index=True,
        nullable=False,
    )
    phone_number: Mapped[str] = mapped_column(
        String(20),
        index=True,
        nullable=False,
    )
    hashed_password: Mapped[str] = mapped_column(String(355), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="userrole", create_constraint=True, validate_strings=True),
        nullable=False,
    )
    profile_picture_url: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, email={self.email}, role={self.role})>"
