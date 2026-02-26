import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.fraud_score_model import FraudScore


class FraudExplanation(Base):
    """
    Explainability layer — one record per feature contributing to a FraudScore.
    Stores SHAP values (ML) and rule trigger weights (rule engine).
    Mandatory for regulatory compliance and analyst review.
    """

    __tablename__ = "fraud_explanations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    fraud_score_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fraud_scores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    explanation: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Human-readable explanation, e.g. 'provider_cost_zscore exceeded 3σ threshold'",
    )
    feature_name: Mapped[Optional[str]] = mapped_column(
        String(100), comment="Machine-readable feature key, e.g. 'duplicate_within_7d'"
    )
    feature_value: Mapped[Optional[str]] = mapped_column(
        String(200), comment="Actual value that triggered this explanation"
    )
    weight: Mapped[Optional[float]] = mapped_column(
        Numeric(8, 4),
        comment="SHAP value or rule weight contributing to the final score",
    )
    source: Mapped[Optional[str]] = mapped_column(
        String(50),
        comment="'rule_engine', 'ml_model', 'DuplicateDetector', 'ProviderProfiler', etc.",
    )

    # Relationship
    fraud_score: Mapped["FraudScore"] = relationship(
        "FraudScore", back_populates="explanations"
    )

    def __repr__(self) -> str:
        return f"<FraudExplanation [{self.source}] {self.feature_name}={self.weight}>"
