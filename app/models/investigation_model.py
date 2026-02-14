import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.fraud_alert_model import FraudAlert
    from app.models.user_model import User


class Investigation(Base):
    """Fraud Investigations"""

    __tablename__ = "investigations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        unique=True,
        nullable=False,
    )
    alert_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("fraud_alerts.id"), unique=True
    )
    investigator_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id")
    )
    # Investigation Details
    investigation_number: Mapped[Optional[str]] = mapped_column(
        String(50), unique=True, index=True
    )
    priority: Mapped[Optional[str]] = mapped_column(
        String(20)
    )  # critical, high, medium, low
    status: Mapped[str] = mapped_column(
        String(50), default="open"
    )  # open, in_progress, completed, closed
    # Findings
    findings: Mapped[Optional[str]] = mapped_column(Text)
    recommendations: Mapped[Optional[str]] = mapped_column(Text)
    action_taken: Mapped[Optional[str]] = mapped_column(Text)
    # Financial Impact
    estimated_fraud_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    actual_fraud_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    recovered_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    # Outcome
    is_fraud_confirmed: Mapped[Optional[bool]] = mapped_column(Boolean)
    prosecution_recommended: Mapped[bool] = mapped_column(Boolean, default=False)
    administrative_action: Mapped[Optional[str]] = mapped_column(Text)
    # Timestamps
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    # Relationships
    alert: Mapped["FraudAlert"] = relationship(
        "FraudAlert", back_populates="investigation"
    )
    investigator: Mapped["User"] = relationship("User", back_populates="investigations")
