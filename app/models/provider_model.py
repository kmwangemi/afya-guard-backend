import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.claim_model import Claim


class Provider(Base):
    """Healthcare Providers/Facilities"""

    __tablename__ = "providers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        unique=True,
        nullable=False,
    )
    provider_code: Mapped[str] = mapped_column(
        String(50), unique=True, index=True, nullable=False
    )
    provider_id_number: Mapped[Optional[str]] = mapped_column(
        String(50), unique=True, index=True
    )  # SHA Provider ID
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    facility_type: Mapped[Optional[str]] = mapped_column(
        String(100)
    )  # Level 2, Level 3, Level 4, Level 5, etc.
    county: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    sub_county: Mapped[Optional[str]] = mapped_column(String(100))
    ward: Mapped[Optional[str]] = mapped_column(String(100))
    physical_address: Mapped[Optional[str]] = mapped_column(Text)
    email: Mapped[Optional[str]] = mapped_column(String(255))
    phone: Mapped[Optional[str]] = mapped_column(String(20))
    capacity: Mapped[Optional[int]] = mapped_column(Integer)  # Number of beds
    accreditation_status: Mapped[Optional[str]] = mapped_column(
        String(50)
    )  # Accredited, Pending, Suspended
    sha_contract_status: Mapped[Optional[str]] = mapped_column(
        String(50)
    )  # Active, Suspended, Terminated
    contract_start_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    contract_end_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # Risk Profiling
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    risk_level: Mapped[Optional[str]] = mapped_column(
        String(20)
    )  # CRITICAL, HIGH, MEDIUM, LOW
    total_claims_count: Mapped[int] = mapped_column(Integer, default=0)
    flagged_claims_count: Mapped[int] = mapped_column(Integer, default=0)
    approved_claims_count: Mapped[int] = mapped_column(Integer, default=0)
    rejected_claims_count: Mapped[int] = mapped_column(Integer, default=0)
    total_claims_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0)
    average_claim_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_blacklisted: Mapped[bool] = mapped_column(Boolean, default=False)
    blacklist_reason: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    claims: Mapped[List["Claim"]] = relationship("Claim", back_populates="provider")

    def __repr__(self) -> str:
        return f"<Provider(id={self.id}, name={self.name}, facility_type={self.facility_type}, county={self.county}, risk_level={self.risk_level})>"
