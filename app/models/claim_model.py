import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, List, Optional

from sqlalchemy import JSON, Boolean, DateTime
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import Float, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums_model import ClaimStatus, PatientDisposition, VisitType

if TYPE_CHECKING:
    from app.models.fraud_alert_model import FraudAlert
    from app.models.patient_model import Patient
    from app.models.provider_model import Provider
    from app.models.user_model import User


class Claim(Base):
    """Healthcare Claims"""

    __tablename__ = "claims"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        unique=True,
        nullable=False,
    )
    claim_number: Mapped[str] = mapped_column(
        String(50), unique=True, index=True, nullable=False
    )
    # Provider Information
    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("providers.id"), index=True
    )
    provider_code: Mapped[Optional[str]] = mapped_column(String(50), index=True)
    # Patient Information
    patient_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("patients.id"), nullable=True
    )
    patient_national_id: Mapped[Optional[str]] = mapped_column(
        String(20), index=True
    )  # Plain text for verification
    patient_sha_number: Mapped[Optional[str]] = mapped_column(String(50), index=True)
    patient_full_name: Mapped[Optional[str]] = mapped_column(String(255))
    # Visit Information
    visit_type: Mapped[Optional[VisitType]] = mapped_column(SQLEnum(VisitType))
    visit_admission_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime, index=True
    )
    discharge_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    op_ip_number: Mapped[Optional[str]] = mapped_column(
        String(50)
    )  # Outpatient/Inpatient Number
    is_new_visit: Mapped[Optional[bool]] = mapped_column(Boolean)
    # Medical Information
    admission_diagnosis: Mapped[Optional[str]] = mapped_column(Text)
    admission_diagnosis_codes: Mapped[Optional[Any]] = mapped_column(
        JSON
    )  # Array of ICD-11 codes
    discharge_diagnosis: Mapped[Optional[str]] = mapped_column(Text)
    discharge_diagnosis_codes: Mapped[Optional[Any]] = mapped_column(
        JSON
    )  # Array of ICD-11 codes
    procedures: Mapped[Optional[Any]] = mapped_column(
        JSON
    )  # Array of procedures with codes and dates
    rendering_physician_name: Mapped[Optional[str]] = mapped_column(String(255))
    rendering_physician_reg_no: Mapped[Optional[str]] = mapped_column(String(50))
    accommodation_type: Mapped[Optional[str]] = mapped_column(String(100))
    # Referral Information
    is_referred: Mapped[bool] = mapped_column(Boolean, default=False)
    referring_facility: Mapped[Optional[str]] = mapped_column(String(255))
    referral_reason: Mapped[Optional[str]] = mapped_column(Text)
    # Discharge Information
    patient_disposition: Mapped[Optional[PatientDisposition]] = mapped_column(
        SQLEnum(PatientDisposition)
    )
    referred_to_facility: Mapped[Optional[str]] = mapped_column(String(255))
    referral_out_reason: Mapped[Optional[str]] = mapped_column(Text)
    # Financial Information
    claim_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    approved_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    service_date: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    submission_date: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # Document Information
    original_file_path: Mapped[Optional[str]] = mapped_column(
        String(500)
    )  # Path to uploaded file
    original_file_type: Mapped[Optional[str]] = mapped_column(
        String(50)
    )  # PDF, Excel, DOCX
    supporting_documents: Mapped[Optional[Any]] = mapped_column(
        JSON
    )  # Array of document paths
    # Fraud Detection Results
    risk_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    is_flagged: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    fraud_flags: Mapped[Optional[Any]] = mapped_column(
        JSON
    )  # Array of fraud indicators
    analysis_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # Processing Status
    status: Mapped[ClaimStatus] = mapped_column(
        SQLEnum(ClaimStatus), default=ClaimStatus.PENDING, index=True
    )
    processing_notes: Mapped[Optional[str]] = mapped_column(Text)
    approved_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text)
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    provider: Mapped["Provider"] = relationship("Provider", back_populates="claims")
    patient: Mapped[Optional["Patient"]] = relationship(
        "Patient", back_populates="claims"
    )
    fraud_alerts: Mapped[List["FraudAlert"]] = relationship(
        "FraudAlert", back_populates="claim"
    )
    approved_by_user: Mapped[Optional["User"]] = relationship(
        "User", back_populates="approved_claims"
    )

    def __repr__(self) -> str:
        return f"<Claim(id={self.id}, claim_number={self.claim_number}, risk_score={self.risk_score}, is_flagged={self.is_flagged})>"
