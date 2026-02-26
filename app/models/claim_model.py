import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Numeric, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums_model import ClaimStatus, ClaimType

if TYPE_CHECKING:
    from app.models.claim_feature_model import ClaimFeature
    from app.models.claim_service_model import ClaimService
    from app.models.fraud_case_model import FraudCase
    from app.models.fraud_score_model import FraudScore
    from app.models.member_model import Member
    from app.models.provider_model import Provider


class Claim(Base):
    """
    Central anchor table — a claim submitted to SHA.
    Raw payload stored as JSONB for full audit compliance.
    Never modify SHA source data through this table.
    """

    __tablename__ = "claims"
    __table_args__ = (
        Index("idx_claims_provider", "provider_id"),
        Index("idx_claims_member", "member_id"),
        Index("idx_claims_submitted", "submitted_at"),
        Index("idx_claims_status", "sha_status"),
        # GIN index for array-type diagnosis_codes search
        Index("idx_claims_diagnosis", "diagnosis_codes", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # SHA reference
    sha_claim_id: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
        index=True,
        comment="SHA-issued claim reference number",
    )
    # Foreign keys
    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("providers.id"), nullable=False
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id"), nullable=False
    )
    # Claim metadata
    claim_type: Mapped[Optional[ClaimType]] = mapped_column(
        Enum(ClaimType, name="claim_type_enum"), index=True
    )
    sha_status: Mapped[ClaimStatus] = mapped_column(
        Enum(ClaimStatus, name="claim_status_enum"),
        default=ClaimStatus.SUBMITTED,
        index=True,
    )
    # Admission / discharge
    admission_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    discharge_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Clinical coding
    diagnosis_codes: Mapped[Optional[List[str]]] = mapped_column(
        ARRAY(String),
        comment="ICD-10 diagnosis codes — indexed with GIN for fast array search",
    )
    # Financials
    total_claim_amount: Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    approved_amount: Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    # Timestamps
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    # Full JSON snapshot of original SHA payload
    raw_payload: Mapped[Optional[dict]] = mapped_column(
        JSONB, comment="Immutable original claim payload from SHA API"
    )

    # Relationships
    provider: Mapped["Provider"] = relationship("Provider", back_populates="claims")
    member: Mapped["Member"] = relationship("Member", back_populates="claims")
    services: Mapped[List["ClaimService"]] = relationship(
        "ClaimService", back_populates="claim", cascade="all, delete-orphan"
    )
    features: Mapped[Optional["ClaimFeature"]] = relationship(
        "ClaimFeature",
        back_populates="claim",
        uselist=False,
        cascade="all, delete-orphan",
    )
    fraud_scores: Mapped[List["FraudScore"]] = relationship(
        "FraudScore", back_populates="claim", cascade="all, delete-orphan"
    )
    fraud_case: Mapped[Optional["FraudCase"]] = relationship(
        "FraudCase", back_populates="claim", uselist=False
    )

    def __repr__(self) -> str:
        return f"<Claim {self.sha_claim_id} [{self.sha_status}]>"
