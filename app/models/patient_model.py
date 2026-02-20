"""
Provider and Patient models
Aligned to:
  - SHA 2023 claim form field names
  - Updated Claim model (total_claim_amount, no claim_amount / service_date)
  - ProviderProfiler (uses provider.provider_id, rejection_rate, risk_level)
  - PhantomPatientDetector (uses sha_number, sha registry — not IPRS)
  - DuplicateDetector (queries Patient by sha_number)
"""

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.claim_model import Claim


class Patient(Base):
    """Patient record — minimal PII for privacy, used for cross-claim fraud detection."""

    __tablename__ = "patients"

    # ── Primary key ───────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        unique=True,
        nullable=False,
    )

    # ── Identifiers ───────────────────────────────────────────────────────────
    sha_number: Mapped[Optional[str]] = mapped_column(
        String(50),
        unique=True,
        index=True,
        comment="SHA Membership Number — primary identifier on the 2023 claim form",
    )
    national_id_hash: Mapped[Optional[str]] = mapped_column(
        String(64),
        unique=True,
        index=True,
        nullable=True,
        comment="SHA-256 hash of National ID — legacy field, not on SHA 2023 form",
    )

    # ── Name fields ───────────────────────────────────────────────────────────
    last_name: Mapped[Optional[str]] = mapped_column(String(100))
    first_name: Mapped[Optional[str]] = mapped_column(String(100))
    middle_name: Mapped[Optional[str]] = mapped_column(String(100))

    # ── Demographics ──────────────────────────────────────────────────────────
    age_group: Mapped[Optional[str]] = mapped_column(
        String(20),
        comment="0-5 | 6-18 | 19-35 | 36-50 | 51-65 | 66+",
    )
    gender: Mapped[Optional[str]] = mapped_column(
        String(10),
        comment="M | F — used by PhantomPatientDetector medical plausibility check",
    )
    county: Mapped[Optional[str]] = mapped_column(
        String(100),
        comment="Home county — used for geographic impossibility cross-check",
    )
    relationship_to_principal: Mapped[Optional[str]] = mapped_column(
        String(50),
        comment="Principal | Spouse | Child | Dependant",
    )

    # ── Status ────────────────────────────────────────────────────────────────
    is_deceased: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    death_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime, comment="Set when SHA registry confirms member is deceased"
    )

    # sha_verified replaces is_verified + iprs_verified_at.
    # Verification is against the SHA member registry, not IPRS.
    sha_verified: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="True if SHA registry confirmed this member exists and is active",
    )
    sha_verified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, comment="Timestamp of last successful SHA registry verification"
    )
    sha_registry_response: Mapped[Optional[str]] = mapped_column(
        Text, comment="Last raw JSON response from SHA registry (audit trail)"
    )

    # ── Activity tracking ─────────────────────────────────────────────────────
    last_claim_date: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    total_claims_count: Mapped[int] = mapped_column(Integer, default=0)

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    claims: Mapped[List["Claim"]] = relationship("Claim", back_populates="patient")

    def __repr__(self) -> str:
        return (
            f"<Patient("
            f"sha_number={self.sha_number}, "
            f"age_group={self.age_group}, "
            f"gender={self.gender}, "
            f"county={self.county}, "
            f"is_deceased={self.is_deceased}, "
            f"sha_verified={self.sha_verified}"
            f")>"
        )
