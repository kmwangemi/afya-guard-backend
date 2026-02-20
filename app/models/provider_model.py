import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from app.core.database import Base

# TYPE_CHECKING block — used ONLY for type hints, never executed at runtime.
# This breaks the circular import:
#   claim_model imports Provider/Patient via TYPE_CHECKING
#   provider_patient_model must also import Claim via TYPE_CHECKING
# Any import outside this block that touches claim_model will cause a circular
# import and break SQLAlchemy's column registration, producing both errors above.
if TYPE_CHECKING:
    from app.models.claim_model import Claim


class Provider(Base):
    """SHA-contracted Health Care Provider / Facility."""

    __tablename__ = "providers"

    # ── Primary key ───────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        unique=True,
        nullable=False,
    )

    # ── SHA identification ────────────────────────────────────────────────────
    provider_id: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        index=True,
        nullable=False,
        comment="SHA Provider Identification Number — Form Field 1",
    )
    provider_code: Mapped[Optional[str]] = mapped_column(
        String(50),
        unique=True,
        index=True,
        nullable=True,
        comment="Internal provider code (legacy/display use only)",
    )

    # ── Basic details ─────────────────────────────────────────────────────────
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    facility_type: Mapped[Optional[str]] = mapped_column(
        String(100),
        comment="Level 2 | Level 3 | Level 4 | Level 5 | Private Hospital | Clinic",
    )
    county: Mapped[Optional[str]] = mapped_column(
        String(100),
        index=True,
        comment="Used by PhantomPatientDetector for geographic impossibility checks",
    )
    sub_county: Mapped[Optional[str]] = mapped_column(String(100))
    ward: Mapped[Optional[str]] = mapped_column(String(100))
    physical_address: Mapped[Optional[str]] = mapped_column(Text)
    email: Mapped[Optional[str]] = mapped_column(String(255))
    phone: Mapped[Optional[str]] = mapped_column(String(20))
    capacity: Mapped[Optional[int]] = mapped_column(Integer, comment="Number of beds")

    # ── Accreditation & contract ──────────────────────────────────────────────
    accreditation_status: Mapped[Optional[str]] = mapped_column(
        String(50), comment="Accredited | Pending | Suspended"
    )
    sha_contract_status: Mapped[Optional[str]] = mapped_column(
        String(50), comment="Active | Suspended | Terminated"
    )
    contract_start_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    contract_end_date: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # ── Risk profiling ────────────────────────────────────────────────────────
    risk_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    risk_level: Mapped[Optional[str]] = mapped_column(
        String(20),
        index=True,
        comment="CRITICAL | HIGH | MEDIUM | LOW — read by ProviderProfiler",
    )
    total_claims_count: Mapped[int] = mapped_column(Integer, default=0)
    flagged_claims_count: Mapped[int] = mapped_column(Integer, default=0)
    approved_claims_count: Mapped[int] = mapped_column(Integer, default=0)
    rejected_claims_count: Mapped[int] = mapped_column(Integer, default=0)

    total_billed_amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        default=Decimal("0.00"),
        comment="Sum of total_bill_amount across all claims",
    )
    total_claimed_amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        default=Decimal("0.00"),
        comment="Sum of total_claim_amount across all claims",
    )
    average_claimed_amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        default=Decimal("0.00"),
        comment="Rolling average of total_claim_amount",
    )

    # ── Status flags ──────────────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_blacklisted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    blacklist_reason: Mapped[Optional[str]] = mapped_column(Text)
    is_suspended: Mapped[bool] = mapped_column(Boolean, default=False)
    suspension_reason: Mapped[Optional[str]] = mapped_column(Text)
    suspended_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_claim_date: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)

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
    # String-based forward reference — safe against circular imports at runtime
    claims: Mapped[List["Claim"]] = relationship("Claim", back_populates="provider")

    # ── Stats updater ─────────────────────────────────────────────────────────
    def update_stats(self, db: Session) -> None:
        """
        Recompute all aggregate counters from live claim data.
        Call this after a claim status changes (approval, rejection, flagging).

        All imports are local to this method to avoid circular imports at module
        load time. This is the correct pattern when two models reference each other.

        Usage:
            provider.update_stats(db)
            db.commit()
        """
        # Local imports — intentional. Importing Claim at module level creates
        # a circular import that breaks SQLAlchemy column registration and
        # causes Pylint to report "no member" errors on Claim columns.
        from sqlalchemy import case, func  # noqa: PLC0415

        from app.models.claim_model import Claim  # noqa: PLC0415
        from app.models.enums_model import ClaimStatus  # noqa: PLC0415

        row = (
            db.query(
                # func.count() with no args emits COUNT(*) — avoids the Pylint
                # false positive of func.count(Claim.id) being flagged as
                # "count is not callable" when imported at module level.
                func.count().label("total"),
                func.sum(
                    case(
                        (Claim.status == ClaimStatus.FLAGGED_CRITICAL, 1),
                        (Claim.status == ClaimStatus.FLAGGED_REVIEW, 1),
                        else_=0,
                    )
                ).label("flagged"),
                func.sum(
                    case(
                        (Claim.status == ClaimStatus.APPROVED, 1),
                        else_=0,
                    )
                ).label("approved"),
                func.sum(
                    case(
                        (Claim.status == ClaimStatus.REJECTED, 1),
                        else_=0,
                    )
                ).label("rejected"),
                func.coalesce(func.sum(Claim.total_bill_amount), 0).label("billed"),
                func.coalesce(func.sum(Claim.total_claim_amount), 0).label("claimed"),
                func.coalesce(func.avg(Claim.total_claim_amount), 0).label(
                    "avg_claimed"
                ),
                func.max(Claim.visit_admission_date).label("last_claim"),
            )
            # Filter by the Provider DB primary key (uuid), not provider_id string
            .filter(Claim.provider_id == self.id).one()
        )

        self.total_claims_count = row.total or 0
        self.flagged_claims_count = row.flagged or 0
        self.approved_claims_count = row.approved or 0
        self.rejected_claims_count = row.rejected or 0
        self.total_billed_amount = Decimal(str(row.billed))
        self.total_claimed_amount = Decimal(str(row.claimed))
        self.average_claimed_amount = Decimal(str(row.avg_claimed))
        self.last_claim_date = row.last_claim

        # Derive risk_level from computed rejection rate
        if self.total_claims_count >= 10:
            rate = self.rejected_claims_count / self.total_claims_count
            if rate > 0.5 or self.is_blacklisted:
                self.risk_level = "CRITICAL"
            elif rate > 0.3 or self.is_suspended:
                self.risk_level = "HIGH"
            elif rate > 0.15:
                self.risk_level = "MEDIUM"
            else:
                self.risk_level = "LOW"

    def __repr__(self) -> str:
        return (
            f"<Provider("
            f"provider_id={self.provider_id}, "
            f"name={self.name}, "
            f"county={self.county}, "
            f"risk_level={self.risk_level}"
            f")>"
        )
