import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.claim_model import Claim


class ClaimFeature(Base):
    """
    Pre-computed ML features derived from a claim.
    Stored separately so features can be recomputed without touching claims.
    One-to-one with Claim.
    """

    __tablename__ = "claim_features"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("claims.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    # Provider-level features
    provider_avg_cost_90d: Mapped[Optional[float]] = mapped_column(
        Numeric(14, 2), comment="Provider's average claim cost over trailing 90 days"
    )
    provider_cost_zscore: Mapped[Optional[float]] = mapped_column(
        Numeric(10, 4),
        comment="Z-score of this claim's cost vs provider's historical distribution",
    )
    # Member-level features
    member_visits_30d: Mapped[Optional[int]] = mapped_column(
        Integer, comment="Number of facility visits by this member in last 30 days"
    )
    member_visits_7d: Mapped[Optional[int]] = mapped_column(
        Integer, comment="Number of facility visits by this member in last 7 days"
    )
    member_unique_providers_30d: Mapped[Optional[int]] = mapped_column(
        Integer,
        comment="Number of distinct providers visited by member in last 30 days (hopping flag)",
    )
    # Claim-level features
    duplicate_within_7d: Mapped[Optional[bool]] = mapped_column(
        Boolean,
        comment="True if same member + similar diagnosis submitted within 7 days",
    )
    length_of_stay: Mapped[Optional[int]] = mapped_column(
        Integer, comment="Admission to discharge in days (0 for outpatient)"
    )
    weekend_submission: Mapped[Optional[bool]] = mapped_column(
        Boolean, comment="True if claim was submitted on a Saturday or Sunday"
    )
    diagnosis_cost_zscore: Mapped[Optional[float]] = mapped_column(
        Numeric(10, 4),
        comment="Z-score of this claim's cost vs national avg for same ICD-10 diagnosis",
    )
    service_count: Mapped[Optional[int]] = mapped_column(
        Integer, comment="Total number of service line items in this claim"
    )
    has_lab_without_diagnosis: Mapped[Optional[bool]] = mapped_column(
        Boolean,
        comment="True if lab tests billed but no supporting diagnosis code present",
    )
    has_surgery_without_theatre: Mapped[Optional[bool]] = mapped_column(
        Boolean, comment="True if surgery billed but no theatre notes attached"
    )
    engineered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    submitted_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    eligibility_checked: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Relationship
    claim: Mapped["Claim"] = relationship("Claim", back_populates="features")

    def __repr__(self) -> str:
        return (
            f"<ClaimFeature claim={self.claim_id} zscore={self.provider_cost_zscore}>"
        )
