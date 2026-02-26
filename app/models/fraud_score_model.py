import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Optional, List

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums_model import RiskLevel

if TYPE_CHECKING:
    from app.models.claim_model import Claim
    from app.models.fraud_case_model import FraudCase
    from app.models.fraud_explanation_model import FraudExplanation
    from app.models.model_version_model import ModelVersion


class FraudScore(Base):
    """
    A versioned scoring event for a claim.
    Multiple scores can exist per claim (e.g. after rule changes / model update).
    The most recent score is the authoritative one.
    """

    __tablename__ = "fraud_scores"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("claims.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    model_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("model_versions.id"),
        comment="Which ML model version produced this score",
    )
    # Component scores (each 0–100 scale)
    rule_score: Mapped[Optional[float]] = mapped_column(
        Numeric(6, 4), comment="Score from deterministic rule engine (0-100)"
    )
    ml_probability: Mapped[Optional[float]] = mapped_column(
        Numeric(6, 4), comment="ML model fraud probability scaled to 0-100"
    )
    anomaly_score: Mapped[Optional[float]] = mapped_column(
        Numeric(6, 4), comment="Isolation Forest / autoencoder anomaly score (0-100)"
    )
    # Detector scores (stored as JSONB for extensibility)
    detector_scores: Mapped[Optional[dict]] = mapped_column(
        JSONB, comment='Dict of detector name → score, e.g. {"DuplicateDetector": 75.0}'
    )
    # Final aggregated score
    final_score: Mapped[Optional[float]] = mapped_column(
        Numeric(6, 4),
        comment="Weighted aggregate: rule×0.4 + ml×0.4 + avg_detector×0.2",
    )
    risk_level: Mapped[Optional[RiskLevel]] = mapped_column(
        Enum(RiskLevel, name="risk_level_enum"),
        index=True,
        comment="LOW / MEDIUM / HIGH / CRITICAL",
    )
    # Metadata
    scored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    scored_by: Mapped[Optional[str]] = mapped_column(
        String(100), comment="'system' for auto-score, user email for manual override"
    )

    # Relationships
    claim: Mapped["Claim"] = relationship("Claim", back_populates="fraud_scores")
    explanations: Mapped[List["FraudExplanation"]] = relationship(
        "FraudExplanation", back_populates="fraud_score", cascade="all, delete-orphan"
    )
    model_version: Mapped[Optional["ModelVersion"]] = relationship("ModelVersion")
    fraud_case: Mapped[Optional["FraudCase"]] = relationship(
        "FraudCase", back_populates="fraud_score", uselist=False
    )

    def __repr__(self) -> str:
        return f"<FraudScore claim={self.claim_id} final={self.final_score} [{self.risk_level}]>"
