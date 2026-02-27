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
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums_model import AlertSeverity, AlertStatus, AlertType

if TYPE_CHECKING:
    from app.models.alert_notification_model import AlertNotification
    from app.models.claim_model import Claim
    from app.models.fraud_case_model import FraudCase
    from app.models.fraud_score_model import FraudScore
    from app.models.user_model import User


class FraudAlert(Base):
    """
    Real-time alert raised by the fraud engine for a specific claim event.

    Lifecycle:
        OPEN → ACKNOWLEDGED → INVESTIGATING → ESCALATED (creates FraudCase)
                                            → RESOLVED   (false positive / no action)
        OPEN → EXPIRED (if unactioned after auto_expire_hours)

    One claim can have multiple alerts (e.g. both DUPLICATE_CLAIM and HIGH_RISK_SCORE).
    One alert can optionally escalate to one FraudCase.
    """

    __tablename__ = "fraud_alerts"
    __table_args__ = (
        Index("idx_alerts_claim", "claim_id"),
        Index("idx_alerts_status", "status"),
        Index("idx_alerts_severity", "severity"),
        Index("idx_alerts_alert_type", "alert_type"),
        Index("idx_alerts_raised_at", "raised_at"),
        Index("idx_alerts_assigned", "assigned_to"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # ── Core linkage ────────────────────────────────────────────────────────
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("claims.id", ondelete="CASCADE"),
        nullable=False,
        comment="The claim that triggered this alert",
    )
    fraud_score_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fraud_scores.id", ondelete="SET NULL"),
        comment="The specific scoring event that produced this alert (if score-triggered)",
    )
    fraud_case_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fraud_cases.id", ondelete="SET NULL"),
        comment="Set when this alert is escalated to a full investigation case",
    )
    # ── Classification ──────────────────────────────────────────────────────
    alert_type: Mapped[AlertType] = mapped_column(
        Enum(AlertType, name="alert_type_enum"),
        nullable=False,
        comment="What kind of fraud signal triggered this alert",
    )
    severity: Mapped[AlertSeverity] = mapped_column(
        Enum(AlertSeverity, name="alert_severity_enum"),
        nullable=False,
        default=AlertSeverity.WARNING,
        comment="Operational urgency level",
    )
    status: Mapped[AlertStatus] = mapped_column(
        Enum(AlertStatus, name="alert_status_enum"),
        nullable=False,
        default=AlertStatus.OPEN,
        comment="Current lifecycle state of this alert",
    )
    # ── Alert content ───────────────────────────────────────────────────────
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Short human-readable headline, e.g. 'Duplicate claim detected within 5 days'",
    )
    message: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Full alert description with context for the analyst",
    )
    triggered_by: Mapped[Optional[str]] = mapped_column(
        String(100),
        comment="Component that raised this alert, e.g. 'DuplicateDetector', 'RuleEngine', 'MLModel'",
    )
    # Score snapshot at time of alert
    score_at_alert: Mapped[Optional[float]] = mapped_column(
        Numeric(6, 4), comment="The final_score value when this alert was raised"
    )
    # ── Assignment & ownership ──────────────────────────────────────────────
    assigned_to: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        comment="Analyst this alert is routed to (NULL = unassigned)",
    )
    # ── Escalation / expiry ─────────────────────────────────────────────────
    auto_escalate: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="If True, system will auto-create a FraudCase if alert is unacknowledged past deadline",
    )
    auto_escalate_after_hours: Mapped[Optional[int]] = mapped_column(
        Integer,
        comment="Hours after raised_at before auto-escalation triggers (e.g. 24)",
    )
    escalated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        comment="Timestamp when this alert was escalated to a FraudCase",
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        comment="Alert auto-expires (status → EXPIRED) after this time if unactioned",
    )
    # ── Resolution metadata ─────────────────────────────────────────────────
    resolved_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        comment="Analyst who resolved or dismissed this alert",
    )
    resolution_note: Mapped[Optional[str]] = mapped_column(
        Text, comment="Optional note recorded when resolving or dismissing the alert"
    )
    is_false_positive: Mapped[Optional[bool]] = mapped_column(
        Boolean,
        comment="Set True if analyst marks this as a false positive (used for model feedback)",
    )
    # ── Extra context (flexible) ────────────────────────────────────────────
    fraud_alert_metadata: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        comment=(
            "Arbitrary context payload from the triggering detector, e.g. "
            '{"duplicate_claim_ids": ["abc", "def"], "days_between": 3}'
        ),
    )
    # ── Timestamps ──────────────────────────────────────────────────────────
    raised_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # ── Relationships ────────────────────────────────────────────────────────

    claim: Mapped["Claim"] = relationship(
        "Claim",
        foreign_keys=[claim_id],
        backref="fraud_alerts",
    )
    fraud_score: Mapped[Optional["FraudScore"]] = relationship(
        "FraudScore",
        foreign_keys=[fraud_score_id],
        backref="alerts",
    )
    fraud_case: Mapped[Optional["FraudCase"]] = relationship(
        "FraudCase",
        foreign_keys=[fraud_case_id],
        backref="alerts",
    )
    assigned_analyst: Mapped[Optional["User"]] = relationship(
        "User",
        foreign_keys=[assigned_to],
        backref="assigned_alerts",
    )
    resolver: Mapped[Optional["User"]] = relationship(
        "User",
        foreign_keys=[resolved_by],
    )
    notifications: Mapped[List["AlertNotification"]] = relationship(
        "AlertNotification",
        back_populates="alert",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<FraudAlert [{self.alert_type}] "
            f"severity={self.severity} "
            f"status={self.status} "
            f"claim={self.claim_id}>"
        )
