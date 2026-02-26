from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.association_tables_model import role_permissions

if TYPE_CHECKING:
    from app.models.role_model import Role


class Permission(Base):
    """
    Fine-grained permission string.
    Examples: 'view_claim', 'score_claim', 'create_case', 'deploy_model'.
    """

    __tablename__ = "permissions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
        comment="Permission code, e.g. 'create_case'",
    )
    description: Mapped[Optional[str]] = mapped_column(Text)
    category: Mapped[Optional[str]] = mapped_column(
        String(50), comment="'claims' | 'fraud' | 'cases' | 'admin' | 'analytics'"
    )

    # Relationship
    roles: Mapped[List["Role"]] = relationship(
        "Role", secondary=role_permissions, back_populates="permissions"
    )

    def __repr__(self) -> str:
        return f"<Permission '{self.name}'>"
