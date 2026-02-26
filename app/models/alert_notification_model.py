"""
SHA Fraud Detection System — FraudAlert Model

A FraudAlert is a real-time notification event raised by the fraud engine
whenever a claim crosses a risk threshold or a specific detector fires.

Alerts differ from FraudCases in an important way:
  - FraudCase   = a structured investigation assigned to an analyst (manual workflow)
  - FraudAlert  = an instant, system-generated notification (automated, push-based)

Alerts can:
  - Be sent to analysts via the dashboard, email, or webhook
  - Be acknowledged (dismissed) without opening a full case
  - Automatically escalate to a FraudCase if unacknowledged past a deadline
  - Be linked to a specific FraudScore, Claim, Provider, or Member
  - Track delivery status per notification channel
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums_model import AlertChannel, DeliveryStatus

if TYPE_CHECKING:
    from app.models.fraud_alert_model import FraudAlert


# ============================================================================
# ALERT NOTIFICATION DELIVERY LOG
# ============================================================================


class AlertNotification(Base):
    """
    Tracks each delivery attempt for a FraudAlert across channels.

    One FraudAlert can generate multiple notifications:
      - dashboard notification
      - email to the assigned analyst
      - webhook POST to SHA integration endpoint

    This table gives full delivery traceability per channel.
    """

    __tablename__ = "alert_notifications"
    __table_args__ = (
        Index("idx_notif_alert", "alert_id"),
        Index("idx_notif_channel", "channel"),
        Index("idx_notif_status", "delivery_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    alert_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fraud_alerts.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Channel config
    channel: Mapped[AlertChannel] = mapped_column(
        Enum(AlertChannel, name="alert_channel_enum"),
        nullable=False,
    )
    recipient: Mapped[Optional[str]] = mapped_column(
        String(255),
        comment="Email address, phone number, webhook URL, or Slack channel",
    )
    # Delivery tracking
    delivery_status: Mapped[DeliveryStatus] = mapped_column(
        Enum(DeliveryStatus, name="delivery_status_enum"),
        nullable=False,
        default=DeliveryStatus.PENDING,
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, default=0, comment="Number of delivery attempts made"
    )
    last_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # Response / error detail
    response_code: Mapped[Optional[int]] = mapped_column(
        Integer, comment="HTTP response code for webhook deliveries"
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        Text, comment="Error detail if delivery failed"
    )
    # Full request/response payload for debugging
    payload: Mapped[Optional[dict]] = mapped_column(
        JSONB, comment="Request body sent to the channel endpoint"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    # Relationship
    alert: Mapped["FraudAlert"] = relationship(
        "FraudAlert", back_populates="notifications"
    )

    def __repr__(self) -> str:
        return (
            f"<AlertNotification alert={self.alert_id} "
            f"channel={self.channel} "
            f"status={self.delivery_status}>"
        )
