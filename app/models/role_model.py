from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.association_tables_model import user_roles, role_permissions

if TYPE_CHECKING:
    from app.models.permission_model import Permission
    from app.models.user_model import User


class Role(Base):
    """
    Named role grouping a set of permissions.
    Default roles: Fraud Analyst, Senior Analyst, Admin, Data Scientist, Auditor.
    """

    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
        comment="Role name, e.g. 'fraud_analyst'",
    )
    display_name: Mapped[Optional[str]] = mapped_column(String(200))
    description: Mapped[Optional[str]] = mapped_column(Text)
    is_system_role: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="System roles cannot be deleted via the UI"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    # Relationships
    users: Mapped[List["User"]] = relationship(
        "User", secondary=user_roles, back_populates="roles"
    )
    permissions: Mapped[List["Permission"]] = relationship(
        "Permission", secondary=role_permissions, back_populates="roles"
    )

    def __repr__(self) -> str:
        return f"<Role '{self.name}'>"
