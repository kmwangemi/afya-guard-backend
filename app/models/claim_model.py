import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, List, Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Numeric, String, Text
from sqlalchemy import Enum as SQLEnum
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
    """
    SHA Healthcare Claim
    Aligned to: Social Health Insurance Act 2023 claim form,
    SHAClaimData extractor, claims_router, and all fraud detection services.
    """

    __tablename__ = "claims"

    # ── Primary key ───────────────────────────────────────────────────────────
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
    # ── Source file (Cloudinary) ──────────────────────────────────────────────
    # Replaces original_file_path + original_file_type.
    # Every claim must link back to the uploaded document on Cloudinary.
    source_file_url: Mapped[Optional[str]] = mapped_column(
        String(500), comment="Cloudinary secure_url of the uploaded claim file"
    )
    source_file_public_id: Mapped[Optional[str]] = mapped_column(
        String(255), comment="Cloudinary public_id — used to delete or replace the file"
    )
    # ── Part I: Provider Information ──────────────────────────────────────────
    # provider_id (FK) = internal DB uuid of the Provider record
    # provider_code    = SHA provider identification number from the claim form (Field 1)
    # provider_name    = name of health care provider/facility (Field 2)
    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("providers.id"), index=True, nullable=False
    )
    provider_code: Mapped[Optional[str]] = mapped_column(
        String(50),
        index=True,
        comment="SHA Provider Identification Number (Form Field 1)",
    )
    provider_name: Mapped[Optional[str]] = mapped_column(
        String(255), comment="Name of Health Care Provider/Facility (Form Field 2)"
    )
    # ── Part II: Patient Information ──────────────────────────────────────────
    # patient_id is the FK to the Patient table (optional — may not exist yet)
    # The SHA 2023 form uses sha_number as the primary patient identifier.
    # There is NO National ID field on the form; patient_national_id is removed.
    patient_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("patients.id"), nullable=True
    )
    patient_sha_number: Mapped[Optional[str]] = mapped_column(
        String(50),
        index=True,
        comment="Social Health Authority Number (Form Field 4) — primary patient identifier",
    )
    # Name stored as three separate fields to match the form's layout (Form Field 3)
    patient_last_name: Mapped[Optional[str]] = mapped_column(String(100))
    patient_first_name: Mapped[Optional[str]] = mapped_column(String(100))
    patient_middle_name: Mapped[Optional[str]] = mapped_column(String(100))
    # Convenience property — assembled by the extractor; stored for fast display/search
    patient_full_name: Mapped[Optional[str]] = mapped_column(String(300))
    patient_residence: Mapped[Optional[str]] = mapped_column(
        String(255), comment="Residence (Form Field 5)"
    )
    other_insurance: Mapped[Optional[str]] = mapped_column(
        String(255), comment="Other health insurance if any (Form Field 6)"
    )
    relationship_to_principal: Mapped[Optional[str]] = mapped_column(
        String(100), comment="Relationship to Principal (Form Field 7)"
    )
    # ── Part III: Visit Information ───────────────────────────────────────────

    # Referral — incoming (Form Field 7 / Part III)
    # was_referred replaces is_referred.
    # Nullable: None = not recorded, True = referred, False = not referred.
    # Default False on the old model was wrong — absence of a value ≠ "not referred".
    was_referred: Mapped[Optional[bool]] = mapped_column(
        Boolean,
        nullable=True,
        comment="Whether patient was referred by another provider (Form Field 7)",
    )
    referral_provider: Mapped[Optional[str]] = mapped_column(
        String(255), comment="Name of referring Health Care Provider/Facility"
    )
    # Visit type & dates
    visit_type: Mapped[Optional[VisitType]] = mapped_column(
        SQLEnum(VisitType), comment="Inpatient | Outpatient | Day-care"
    )
    visit_admission_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        index=True,
        comment="Visit/Admission Date — primary date field; service_date is removed",
    )
    discharge_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    op_ip_number: Mapped[Optional[str]] = mapped_column(
        String(50), comment="OP/IP Number"
    )
    # new_or_return_visit replaces is_new_visit (bool).
    # The SHA form captures "New" or "Return" as text, not a boolean.
    new_or_return_visit: Mapped[Optional[str]] = mapped_column(
        String(20), comment="New | Return (Form Part III)"
    )
    # rendering_physician is a single combined field on the SHA form:
    # "Rendering Physician Name and Registration No."
    # Replaces rendering_physician_name + rendering_physician_reg_no.
    rendering_physician: Mapped[Optional[str]] = mapped_column(
        String(300),
        comment="Rendering Physician Name and Registration No. (combined, Form Part III)",
    )
    # Accommodation type (Form Part III)
    # Valid values: Female Medical, Male Medical, Female Surgical, Male Surgical,
    # Gynaecology, Maternity, NBU, Psychiatric Unit, Burns, ICU, HDU, NICU, Isolation
    accommodation_type: Mapped[Optional[str]] = mapped_column(String(100))
    # ── Field 9: Patient Disposition ─────────────────────────────────────────
    patient_disposition: Mapped[Optional[PatientDisposition]] = mapped_column(
        SQLEnum(PatientDisposition),
        comment="Improved | Recovered | LAMA | Absconded | Died (Form Field 9)",
    )
    # ── Field 10: Discharge Referral ─────────────────────────────────────────
    # Distinct from the incoming referral above.
    # Replaces referred_to_facility + referral_out_reason.
    discharge_referral_institution: Mapped[Optional[str]] = mapped_column(
        String(255), comment="Name of Referral Institution on discharge (Form Field 10)"
    )
    discharge_referral_reason: Mapped[Optional[str]] = mapped_column(
        Text, comment="Reason/s for discharge referral (Form Field 10)"
    )
    # ── Fields 11 & 12: Diagnoses ─────────────────────────────────────────────
    # admission_diagnosis_codes, discharge_diagnosis_codes, and procedures
    # (three JSON columns) are removed — replaced by the fields below which
    # match the extractor's output directly.
    admission_diagnosis: Mapped[Optional[str]] = mapped_column(
        Text, comment="Admission Diagnosis/es (Form Field 11)"
    )
    discharge_diagnosis: Mapped[Optional[str]] = mapped_column(
        Text, comment="Discharge Diagnosis (Form Field 12)"
    )
    icd11_code: Mapped[Optional[str]] = mapped_column(
        String(50), comment="ICD-11 Code/s (Form Field 12)"
    )
    related_procedure: Mapped[Optional[str]] = mapped_column(
        Text, comment="Related Procedure/s if any (Form Field 12)"
    )
    procedure_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime, comment="Date of Procedure (Form Field 12)"
    )
    # ── Field 14: SHA Health Benefits Table ──────────────────────────────────
    # Replaces the removed `procedures` JSON column.
    # Stored as a JSON array; each element is a dict with keys:
    #   admission_date, discharge_date, case_code, icd11_procedure_code,
    #   description, preauth_no, bill_amount, claim_amount
    benefit_lines: Mapped[Optional[Any]] = mapped_column(
        JSON, comment="SHA Health Benefits table rows (Form Field 14)"
    )
    # Financial totals derived from benefit_lines by the extractor.
    # Replaces the single claim_amount (nullable=False) which had no basis on the form.
    total_bill_amount: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(15, 2), comment="Sum of bill_amount across all benefit lines"
    )
    total_claim_amount: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(15, 2),
        index=True,
        comment="Sum of claim_amount across all benefit lines",
    )
    # ── Declaration ───────────────────────────────────────────────────────────
    patient_authorised_name: Mapped[Optional[str]] = mapped_column(
        String(300), comment="Names (Majina) from patient/authorised person declaration"
    )
    declaration_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime, comment="Date (Tarehe) from patient declaration"
    )
    # ── Fraud Detection Results ───────────────────────────────────────────────
    risk_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    is_flagged: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    fraud_flags: Mapped[Optional[Any]] = mapped_column(
        JSON, comment="Array of fraud flag dicts from detection modules"
    )
    analysis_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # ── Processing Status ─────────────────────────────────────────────────────
    status: Mapped[ClaimStatus] = mapped_column(
        SQLEnum(ClaimStatus), default=ClaimStatus.PENDING, index=True
    )
    processing_notes: Mapped[Optional[str]] = mapped_column(Text)
    approved_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    approved_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text)
    # ── Submission Tracking ───────────────────────────────────────────────────
    submitted_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
        comment="User who submitted the claim via the API",
    )
    submission_date: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
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
    provider: Mapped["Provider"] = relationship("Provider", back_populates="claims")
    patient: Mapped[Optional["Patient"]] = relationship(
        "Patient", back_populates="claims"
    )
    fraud_alerts: Mapped[List["FraudAlert"]] = relationship(
        "FraudAlert", back_populates="claim"
    )
    approved_by_user: Mapped[Optional["User"]] = relationship(
        "User",
        foreign_keys=[approved_by],
        back_populates="approved_claims",
    )
    submitted_by_user: Mapped[Optional["User"]] = relationship(
        "User",
        foreign_keys=[submitted_by],
        back_populates="submitted_claims",
    )

    def __repr__(self) -> str:
        return (
            f"<Claim("
            f"id={self.id}, "
            f"claim_number={self.claim_number}, "
            f"sha_number={self.patient_sha_number}, "
            f"total_claim_amount={self.total_claim_amount}, "
            f"risk_score={self.risk_score}, "
            f"is_flagged={self.is_flagged}"
            f")>"
        )
