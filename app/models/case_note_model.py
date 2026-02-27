from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.fraud_case_model import FraudCase
    from app.models.user_model import User


class CaseNote(Base):
    """
    Analyst notes attached to a FraudCase.
    Immutable once written — supports investigation audit trail.
    """

    __tablename__ = "case_notes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fraud_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=False,
    )
    note: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    # Attachments metadata (file URLs stored as JSONB array)
    attachments: Mapped[Optional[list]] = mapped_column(
        JSONB,
        comment='List of attachment metadata, e.g. [{"name": "discharge_summary.pdf", "url": "..."}]',
    )

    # Relationships
    case: Mapped["FraudCase"] = relationship("FraudCase", back_populates="notes")
    author: Mapped["User"] = relationship("User", foreign_keys=[created_by])

    def __repr__(self) -> str:
        return f"<CaseNote case={self.case_id} by={self.created_by}>"
