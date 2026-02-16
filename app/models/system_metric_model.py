import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import DateTime, Float, Integer, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SystemMetric(Base):
    """System Performance Metrics"""

    __tablename__ = "system_metrics"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        unique=True,
        nullable=False,
    )
    metric_date: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    # Claim Metrics
    total_claims: Mapped[int] = mapped_column(Integer, default=0)
    flagged_claims: Mapped[int] = mapped_column(Integer, default=0)
    critical_alerts: Mapped[int] = mapped_column(Integer, default=0)
    auto_approved_claims: Mapped[int] = mapped_column(Integer, default=0)
    # Financial Metrics
    total_claim_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0)
    flagged_claim_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0)
    estimated_fraud_prevented: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), default=0
    )
    # Detection Metrics
    false_positive_rate: Mapped[Optional[float]] = mapped_column(Float)
    false_negative_rate: Mapped[Optional[float]] = mapped_column(Float)
    detection_accuracy: Mapped[Optional[float]] = mapped_column(Float)
    # Performance Metrics
    avg_processing_time_seconds: Mapped[Optional[float]] = mapped_column(Float)
    avg_investigation_time_hours: Mapped[Optional[float]] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    def __repr__(self) -> str:
        return f"<SystemMetric(id={self.id}, metric_date={self.metric_date}, total_claims={self.total_claims}, flagged_claims={self.flagged_claims}, critical_alerts={self.critical_alerts})>"
