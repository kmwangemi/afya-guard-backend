from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.association_tables_model import user_roles

if TYPE_CHECKING:
    from app.models.audit_log_model import AuditLog
    from app.models.refresh_token_model import RefreshToken
    from app.models.role_model import Role


class User(Base):
    """
    System user — fraud analysts, admins, data scientists, auditors.
    Supports RBAC via many-to-many Role relationship.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Identity
    email: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(20))
    # Auth
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_superuser: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="Superuser bypasses all permission checks"
    )
    # Last login tracking
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    failed_login_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        comment="Consecutive failed login attempts — lock account after threshold",
    )
    locked_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        comment="Account locked until this datetime after too many failed logins",
    )
    # Password policy
    password_changed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    roles: Mapped[List["Role"]] = relationship(
        "Role", secondary=user_roles, back_populates="users"
    )
    refresh_tokens: Mapped[List["RefreshToken"]] = relationship(
        "RefreshToken", back_populates="user", cascade="all, delete-orphan"
    )
    audit_logs: Mapped[List["AuditLog"]] = relationship(
        "AuditLog", back_populates="user"
    )

    def has_permission(self, permission_name: str) -> bool:
        """Check if user holds a specific permission via any of their roles."""
        if self.is_superuser:
            return True
        return any(
            perm.name == permission_name
            for role in self.roles
            for perm in role.permissions
        )

    def __repr__(self) -> str:
        return f"<User {self.email} active={self.is_active}>"
