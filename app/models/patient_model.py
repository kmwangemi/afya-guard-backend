import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.claim_model import Claim


class Patient(Base):
    """Patient Records (Minimal PII for Privacy)"""

    __tablename__ = "patients"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        unique=True,
        nullable=False,
    )
    national_id_hash: Mapped[Optional[str]] = mapped_column(
        String(64), unique=True, index=True
    )  # Hashed National ID
    sha_number: Mapped[Optional[str]] = mapped_column(
        String(50), unique=True, index=True
    )  # SHA Membership Number
    age_group: Mapped[Optional[str]] = mapped_column(
        String(20)
    )  # 0-5, 6-18, 19-35, 36-50, 51-65, 66+
    gender: Mapped[Optional[str]] = mapped_column(String(10))  # M, F
    county: Mapped[Optional[str]] = mapped_column(String(100))
    is_deceased: Mapped[bool] = mapped_column(Boolean, default=False)
    death_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    is_verified: Mapped[bool] = mapped_column(
        Boolean, default=False
    )  # IPRS verification status
    iprs_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    claims: Mapped[List["Claim"]] = relationship("Claim", back_populates="patient")

    def __repr__(self) -> str:
        return f"<Patient(id={self.id}, age_group={self.age_group}, gender={self.gender}, county={self.county}, is_deceased={self.is_deceased})>"
