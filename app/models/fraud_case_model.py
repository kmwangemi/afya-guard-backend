from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums_model import CasePriority, CaseStatus

if TYPE_CHECKING:
    from app.models.case_note_model import CaseNote
    from app.models.claim_model import Claim
    from app.models.fraud_score_model import FraudScore
    from app.models.user_model import User


class FraudCase(Base):
    """
    An investigation case opened when a claim scores HIGH or CRITICAL.
    Tracks analyst assignment, status transitions, and resolution outcome.
    One case per claim (unique constraint on claim_id).
    """

    __tablename__ = "fraud_cases"
    __table_args__ = (UniqueConstraint("claim_id", name="uq_fraud_cases_claim"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("claims.id"),
        nullable=False,
        index=True,
    )
    fraud_score_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fraud_scores.id"),
        nullable=False,
        comment="The scoring event that triggered this case",
    )
    # Assignment
    assigned_to: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        comment="Fraud analyst responsible for this case",
    )
    # Lifecycle
    status: Mapped[CaseStatus] = mapped_column(
        Enum(CaseStatus, name="case_status_enum"),
        default=CaseStatus.OPEN,
        nullable=False,
        index=True,
    )
    priority: Mapped[CasePriority] = mapped_column(
        Enum(CasePriority, name="case_priority_enum"),
        default=CasePriority.MEDIUM,
        nullable=False,
    )
    # Resolution
    resolution_summary: Mapped[Optional[str]] = mapped_column(
        Text, comment="Analyst's summary when closing the case"
    )
    estimated_loss: Mapped[Optional[float]] = mapped_column(
        Numeric(14, 2), comment="Estimated financial loss if confirmed fraud"
    )
    # Timestamps
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Relationships
    claim: Mapped["Claim"] = relationship("Claim", back_populates="fraud_case")
    fraud_score: Mapped["FraudScore"] = relationship(
        "FraudScore", back_populates="fraud_case"
    )
    assigned_analyst: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[assigned_to]
    )
    notes: Mapped[List["CaseNote"]] = relationship(
        "CaseNote", back_populates="case", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<FraudCase {self.id} [{self.status}] priority={self.priority}>"
