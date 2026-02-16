import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums_model import FraudSeverity

if TYPE_CHECKING:
    from app.models.claim_model import Claim
    from app.models.investigation_model import Investigation
    from app.models.user_model import User


class FraudAlert(Base):
    """Fraud Alerts for Flagged Claims"""

    __tablename__ = "fraud_alerts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        unique=True,
        nullable=False,
    )
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claims.id"), nullable=False
    )
    # Alert Details
    alert_type: Mapped[Optional[str]] = mapped_column(
        String(100), index=True
    )  # phantom_patient, upcoding, duplicate, etc.
    severity: Mapped[Optional[FraudSeverity]] = mapped_column(
        SQLEnum(FraudSeverity), index=True
    )
    description: Mapped[Optional[str]] = mapped_column(Text)
    evidence: Mapped[Optional[Any]] = mapped_column(
        JSON
    )  # Detailed evidence from detection modules
    # Module Information
    detection_module: Mapped[Optional[str]] = mapped_column(
        String(50)
    )  # Which module detected this
    module_confidence: Mapped[Optional[float]] = mapped_column(
        Float
    )  # Confidence score from ML models
    # Assignment
    assigned_to: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(50), default="open", index=True
    )  # open, investigating, resolved, false_positive
    priority: Mapped[str] = mapped_column(
        String(20), default="medium"
    )  # critical, high, medium, low
    # Resolution
    resolution_notes: Mapped[Optional[str]] = mapped_column(Text)
    is_confirmed_fraud: Mapped[Optional[bool]] = mapped_column(Boolean)
    estimated_fraud_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    actual_fraud_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Relationships
    claim: Mapped["Claim"] = relationship("Claim", back_populates="fraud_alerts")
    assigned_to_user: Mapped[Optional["User"]] = relationship(
        "User", back_populates="assigned_alerts"
    )
    investigation: Mapped[Optional["Investigation"]] = relationship(
        "Investigation", back_populates="alert", uselist=False
    )

    def __repr__(self) -> str:
        return f"<FraudAlert(id={self.id}, claim_id={self.claim_id}, alert_type={self.alert_type}, severity={self.severity}, status={self.status})>"
