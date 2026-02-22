"""
SHA Fraud Detection System — Pydantic Schemas
Covers all seven schemas used by the claims router:
  - BenefitLineSchema      (shared sub-schema for Field 14 benefit lines)
  - ExtractResponse        (POST /upload-and-extract)
  - ClaimCreate            (POST /submit request body)
  - ClaimResponse          (standard claim response — list, submit, status update)
  - ClaimDetailResponse    (GET /{claim_id} — full detail including fraud alerts)
  - BulkUploadResponse     (POST /bulk-upload)
  - RiskScoreResponse      (GET /{claim_id}/risk-score)
  - ClaimUpdate            (PUT /{claim_id}/status request body)

All field names and types are aligned to:
  - Claim SQLAlchemy model (claim_model.py)
  - SHAClaimData extractor output (claim_extractor.py)
  - Claims router field assignments (claims_router.py)
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.enums_model import ClaimStatus, PatientDisposition, VisitType

# ===========================================================================
# Shared sub-schemas
# ===========================================================================


class BenefitLineSchema(BaseModel):
    """
    One row in the SHA Health Benefits table (Form Field 14).
    Maps directly to the dict structure stored in Claim.benefit_lines JSON.
    """

    admission_date: Optional[datetime] = None
    discharge_date: Optional[datetime] = None
    case_code: Optional[str] = Field(None, max_length=50)
    icd11_procedure_code: Optional[str] = Field(None, max_length=50)
    description: Optional[str] = Field(None, max_length=500)
    preauth_no: Optional[str] = Field(None, max_length=100)
    bill_amount: Optional[Decimal] = Field(None, ge=0)
    claim_amount: Optional[Decimal] = Field(None, ge=0)

    @model_validator(mode="after")
    def claim_must_not_exceed_bill(self) -> "BenefitLineSchema":
        """claim_amount cannot exceed bill_amount on the same line."""
        if (
            self.bill_amount is not None
            and self.claim_amount is not None
            and self.claim_amount > self.bill_amount
        ):
            raise ValueError(
                f"claim_amount ({self.claim_amount}) cannot exceed "
                f"bill_amount ({self.bill_amount}) on a benefit line"
            )
        return self

    model_config = {"from_attributes": True}


class FraudFlagSchema(BaseModel):
    """Single fraud flag produced by a detection module."""

    type: str
    severity: str
    description: str
    score: float
    evidence: Optional[Dict[str, Any]] = None

    model_config = {"from_attributes": True}


# ===========================================================================
# ExtractResponse  —  POST /upload-and-extract
# ===========================================================================


class ExtractResponse(BaseModel):
    """
    Returned after a claim file is uploaded to Cloudinary and parsed.
    The frontend stores cloudinary_public_id and cloudinary_url and passes
    them back in ClaimCreate when the user confirms and submits the claim.
    """

    # Cloudinary references — opaque tokens for the frontend
    cloudinary_public_id: str = Field(
        ..., description="Cloudinary public_id of the uploaded file"
    )
    cloudinary_url: str = Field(
        ..., description="Cloudinary secure_url — permanent link to the source file"
    )
    file_size: int = Field(..., description="File size in bytes")

    # Extracted claim data — all fields from SHAClaimData.to_dict()
    extracted: Dict[str, Any] = Field(
        ..., description="Structured data extracted from the claim form"
    )

    # Validation result — frontend should show errors before allowing /submit
    is_valid: bool
    validation_errors: List[str] = Field(
        default_factory=list,
        description="List of validation error messages; empty if is_valid=True",
    )

    model_config = {"from_attributes": True}


# ===========================================================================
# ClaimCreate  —  POST /submit  (request body)
# ===========================================================================


class ClaimCreate(BaseModel):
    """
    Request body for POST /submit.
    The frontend sends this after the user reviews the extracted data.
    All field names match the Claim model columns exactly.
    """

    # ── Cloudinary references (from ExtractResponse) ──────────────────────
    cloudinary_url: str = Field(..., description="Returned by /upload-and-extract")
    cloudinary_public_id: str = Field(
        ..., description="Returned by /upload-and-extract"
    )

    # ── Part I: Provider ──────────────────────────────────────────────────
    provider_id: str = Field(
        ...,
        max_length=50,
        description="SHA Provider Identification Number (Form Field 1)",
    )
    provider_name: Optional[str] = Field(None, max_length=255)

    # ── Part II: Patient ──────────────────────────────────────────────────
    sha_number: Optional[str] = Field(
        None,
        max_length=50,
        description="Social Health Authority Number (Form Field 4)",
    )
    patient_last_name: Optional[str] = Field(None, max_length=100)
    patient_first_name: Optional[str] = Field(None, max_length=100)
    patient_middle_name: Optional[str] = Field(None, max_length=100)
    residence: Optional[str] = Field(None, max_length=255)
    other_insurance: Optional[str] = Field(None, max_length=255)
    relationship_to_principal: Optional[str] = Field(None, max_length=100)

    # ── Part III: Visit ───────────────────────────────────────────────────
    was_referred: Optional[bool] = None
    referral_provider: Optional[str] = Field(None, max_length=255)
    visit_type: Optional[VisitType] = None
    visit_admission_date: Optional[datetime] = None
    op_ip_number: Optional[str] = Field(None, max_length=50)
    new_or_return_visit: Optional[str] = Field(
        None,
        max_length=20,
        description="New | Return",
    )
    discharge_date: Optional[datetime] = None
    rendering_physician: Optional[str] = Field(
        None,
        max_length=300,
        description="Physician name and registration number combined",
    )
    accommodation_type: Optional[str] = Field(None, max_length=100)

    # ── Field 9: Disposition ──────────────────────────────────────────────
    patient_disposition: Optional[PatientDisposition] = None

    # ── Field 10: Discharge referral ──────────────────────────────────────
    discharge_referral_institution: Optional[str] = Field(None, max_length=255)
    discharge_referral_reason: Optional[str] = None

    # ── Fields 11 & 12: Diagnoses ─────────────────────────────────────────
    admission_diagnosis: Optional[str] = None
    discharge_diagnosis: Optional[str] = None
    icd11_code: Optional[str] = Field(None, max_length=50)
    related_procedure: Optional[str] = None
    procedure_date: Optional[datetime] = None

    # ── Field 14: Benefit lines ───────────────────────────────────────────
    benefit_lines: Optional[List[BenefitLineSchema]] = Field(
        default_factory=list,
        description="SHA Health Benefits table rows",
    )
    total_bill_amount: Optional[Decimal] = Field(None, ge=0)
    total_claim_amount: Optional[Decimal] = Field(None, ge=0)

    # ── Declaration ───────────────────────────────────────────────────────
    patient_authorised_name: Optional[str] = Field(None, max_length=300)
    declaration_date: Optional[datetime] = None

    # ── Validators ────────────────────────────────────────────────────────

    @field_validator("new_or_return_visit")
    @classmethod
    def validate_new_or_return(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v.lower() not in {"new", "return"}:
            raise ValueError("new_or_return_visit must be 'New' or 'Return'")
        return v.title() if v else v

    @field_validator("icd11_code")
    @classmethod
    def validate_icd11_format(cls, v: Optional[str]) -> Optional[str]:
        import re

        if v and not re.match(r"^[A-Z][A-Z0-9]{1,5}(?:\.[A-Z0-9]{1,4})*$", v):
            raise ValueError(
                f"'{v}' does not match expected ICD-11 format (e.g. JA00, BA80.1)"
            )
        return v

    @model_validator(mode="after")
    def discharge_after_admission(self) -> "ClaimCreate":
        if (
            self.visit_admission_date
            and self.discharge_date
            and self.discharge_date < self.visit_admission_date
        ):
            raise ValueError("discharge_date cannot be before visit_admission_date")
        return self

    @model_validator(mode="after")
    def total_claim_not_exceed_total_bill(self) -> "ClaimCreate":
        if (
            self.total_bill_amount is not None
            and self.total_claim_amount is not None
            and self.total_claim_amount > self.total_bill_amount
        ):
            raise ValueError("total_claim_amount cannot exceed total_bill_amount")
        return self

    @model_validator(mode="after")
    def referral_provider_required_when_referred(self) -> "ClaimCreate":
        if self.was_referred is True and not self.referral_provider:
            raise ValueError("referral_provider is required when was_referred is True")
        return self

    model_config = {"from_attributes": True}


# ===========================================================================
# ClaimResponse  —  POST /submit, GET /, PUT /{id}/status
# ===========================================================================


class ClaimResponse(BaseModel):
    """
    Standard claim response returned by submit, list, and status-update endpoints.
    Contains all claim fields but excludes the nested fraud alert objects
    (use ClaimDetailResponse for those).
    """

    # Identity
    id: uuid.UUID
    claim_number: str

    # Cloudinary
    source_file_url: Optional[str] = None
    source_file_public_id: Optional[str] = None

    # Part I — Provider
    provider_id: uuid.UUID  # DB FK uuid
    provider_code: Optional[str] = None
    provider_name: Optional[str] = None

    # Part II — Patient
    patient_sha_number: Optional[str] = None
    patient_last_name: Optional[str] = None
    patient_first_name: Optional[str] = None
    patient_middle_name: Optional[str] = None
    patient_full_name: Optional[str] = None
    patient_residence: Optional[str] = None
    other_insurance: Optional[str] = None
    relationship_to_principal: Optional[str] = None

    # Part III — Visit
    was_referred: Optional[bool] = None
    referral_provider: Optional[str] = None
    visit_type: Optional[VisitType] = None
    visit_admission_date: Optional[datetime] = None
    discharge_date: Optional[datetime] = None
    op_ip_number: Optional[str] = None
    new_or_return_visit: Optional[str] = None
    rendering_physician: Optional[str] = None
    accommodation_type: Optional[str] = None

    # Field 9
    patient_disposition: Optional[PatientDisposition] = None

    # Field 10
    discharge_referral_institution: Optional[str] = None
    discharge_referral_reason: Optional[str] = None

    # Fields 11 & 12
    admission_diagnosis: Optional[str] = None
    discharge_diagnosis: Optional[str] = None
    icd11_code: Optional[str] = None
    related_procedure: Optional[str] = None
    procedure_date: Optional[datetime] = None

    # Field 14
    benefit_lines: Optional[List[Dict[str, Any]]] = None
    total_bill_amount: Optional[Decimal] = None
    total_claim_amount: Optional[Decimal] = None

    # Declaration
    patient_authorised_name: Optional[str] = None
    declaration_date: Optional[datetime] = None

    # Fraud detection
    risk_score: float = 0.0
    is_flagged: bool = False
    fraud_flags: Optional[List[Dict[str, Any]]] = None
    analysis_completed_at: Optional[datetime] = None

    # Processing
    status: ClaimStatus
    processing_notes: Optional[str] = None
    approved_amount: Optional[Decimal] = None
    approved_by: Optional[uuid.UUID] = None
    approved_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None

    # Submission tracking
    submitted_by: Optional[uuid.UUID] = None
    submission_date: datetime

    # Timestamps
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ===========================================================================
# ClaimDetailResponse  —  GET /{claim_id}
# ===========================================================================


class FraudAlertSummary(BaseModel):
    """Embedded fraud alert — used inside ClaimDetailResponse."""

    id: uuid.UUID
    alert_type: str
    severity: str
    description: str
    status: str
    priority: str
    module_confidence: Optional[float] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ClaimDetailResponse(ClaimResponse):
    """
    Full claim detail — extends ClaimResponse with nested fraud alerts.
    Returned by GET /{claim_id}.
    """

    fraud_alerts: List[FraudAlertSummary] = Field(default_factory=list)

    model_config = {"from_attributes": True}


# ===========================================================================
# BulkUploadResponse  —  POST /bulk-upload
# ===========================================================================


class BulkUploadRowError(BaseModel):
    """Error detail for a single failed row in a bulk upload."""

    row: int = Field(..., description="1-indexed row number in the uploaded file")
    error: str


class BulkUploadResponse(BaseModel):
    """
    Returned by POST /bulk-upload.
    Reports per-row success and failure counts alongside the
    Cloudinary URL of the uploaded bulk file for audit purposes.
    """

    uploaded_count: int = Field(
        ..., description="Number of claims successfully inserted"
    )
    claim_ids: List[uuid.UUID] = Field(
        default_factory=list,
        description="DB IDs of successfully created claims",
    )
    failed_count: int = Field(..., description="Number of rows that failed")
    errors: List[BulkUploadRowError] = Field(
        default_factory=list,
        description="Per-row error details for failed rows",
    )
    bulk_file_url: str = Field(
        ..., description="Cloudinary URL of the uploaded bulk file (audit trail)"
    )
    message: str

    model_config = {"from_attributes": True}


# ===========================================================================
# RiskScoreResponse  —  GET /{claim_id}/risk-score
# ===========================================================================


class RiskScoreResponse(BaseModel):
    """
    Fraud risk assessment summary for a single claim.
    Returned by GET /{claim_id}/risk-score.
    """

    claim_id: uuid.UUID
    claim_number: str
    risk_score: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Composite fraud risk score from 0 (clean) to 100 (critical)",
    )
    is_flagged: bool
    status: ClaimStatus
    fraud_flags: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Array of fraud flag dicts from all detection modules",
    )
    analysis_completed_at: Optional[datetime] = Field(
        None,
        description="None if fraud analysis has not yet completed",
    )

    model_config = {"from_attributes": True}


# ===========================================================================
# ClaimUpdate  —  PUT /{claim_id}/status  (request body)
# ===========================================================================


class ClaimUpdate(BaseModel):
    """
    Request body for PUT /{claim_id}/status.
    All fields are optional — only provided fields are updated.
    The router enforces that approved_amount cannot exceed total_claim_amount.
    """

    status: Optional[ClaimStatus] = Field(None, description="New claim status to set")
    processing_notes: Optional[str] = Field(None, description="Internal reviewer notes")
    approved_amount: Optional[Decimal] = Field(
        None,
        ge=0,
        description=(
            "Amount approved for payment. "
            "Must not exceed the claim's total_claim_amount."
        ),
    )
    rejection_reason: Optional[str] = Field(
        None,
        description="Required when status is set to REJECTED",
    )

    @model_validator(mode="after")
    def rejection_reason_required_on_reject(self) -> "ClaimUpdate":
        if self.status == ClaimStatus.REJECTED and not self.rejection_reason:
            raise ValueError(
                "rejection_reason is required when setting status to REJECTED"
            )
        return self

    @model_validator(mode="after")
    def approved_amount_requires_approved_status(self) -> "ClaimUpdate":
        if (
            self.approved_amount is not None
            and self.status is not None
            and self.status != ClaimStatus.APPROVED
        ):
            raise ValueError(
                "approved_amount should only be set when status is APPROVED"
            )
        return self

    model_config = {"from_attributes": True}
