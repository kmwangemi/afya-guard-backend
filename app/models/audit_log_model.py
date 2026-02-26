from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums_model import AuditAction

if TYPE_CHECKING:
    from app.models.user_model import User


class AuditLog(Base):
    """
    Immutable audit trail — one record per system action.
    Written by AuditService after every state-changing operation.
    Never delete records from this table.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("idx_audit_entity", "entity_type", "entity_id"),
        Index("idx_audit_performed_at", "performed_at"),
        Index("idx_audit_user", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Who
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        comment="NULL for system-initiated actions",
    )
    # What
    action: Mapped[AuditAction] = mapped_column(
        Enum(AuditAction, name="audit_action_enum"),
        nullable=False,
        index=True,
    )
    # Which entity
    entity_type: Mapped[Optional[str]] = mapped_column(
        String(100), comment="e.g. 'Claim', 'FraudCase', 'User'"
    )
    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), comment="PK of the affected record"
    )
    # Context
    audit_log_metadata: Mapped[Optional[dict]] = mapped_column(
        JSONB, comment="Arbitrary context — diff, old/new values, request IP, etc."
    )
    ip_address: Mapped[Optional[str]] = mapped_column(String(45))
    user_agent: Mapped[Optional[str]] = mapped_column(String(500))
    performed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    # Relationship
    user: Mapped[Optional["User"]] = relationship("User", back_populates="audit_logs")

    def __repr__(self) -> str:
        return f"<AuditLog [{self.action}] entity={self.entity_type}:{self.entity_id}>"
