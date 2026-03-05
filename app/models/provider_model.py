import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, Enum, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums_model import AccreditationStatus, FacilityType

if TYPE_CHECKING:
    from app.models.claim_model import Claim


class Provider(Base):
    """
    Healthcare facility accredited with SHA.
    Data is a snapshot pulled from SHA — never modified directly.
    """

    __tablename__ = "providers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # SHA identifiers
    sha_provider_code: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
        index=True,
        comment="Unique provider code assigned by SHA",
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # Location & classification
    county: Mapped[Optional[str]] = mapped_column(String(100))
    sub_county: Mapped[Optional[str]] = mapped_column(String(100))
    facility_type: Mapped[Optional[FacilityType]] = mapped_column(
        Enum(FacilityType, name="facility_type_enum")
    )
    accreditation_status: Mapped[Optional[AccreditationStatus]] = mapped_column(
        Enum(AccreditationStatus, name="accreditation_status_enum"),
        default=AccreditationStatus.ACTIVE,
        index=True,
    )
    # Contact
    phone: Mapped[Optional[str]] = mapped_column(String(20))
    email: Mapped[Optional[str]] = mapped_column(String(255))
    # Profiling (computed by ProviderProfiler detector)
    avg_claim_amount: Mapped[Optional[float]] = mapped_column(
        Numeric(14, 2), comment="Rolling average claim amount for this provider"
    )
    bed_capacity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    peer_avg: Mapped[Optional[float]] = mapped_column(
        Numeric(14, 2),
        comment="Average claim amount for peer providers in same county/type",
    )
    high_risk_flag: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="Set to True if provider has been flagged as high-risk",
    )
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
        return f"<Provider {self.sha_provider_code} — {self.name}>"
