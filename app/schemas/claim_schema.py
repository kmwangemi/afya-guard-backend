"""
SHA Fraud Detection — Claim Schemas

Covers: claim ingestion, service line items, member eligibility, provider info,
        status updates, and SHA webhook event payload.
"""

import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import Field, field_validator

from app.models.enums_model import (
    AccreditationStatus,
    ClaimStatus,
    ClaimType,
    FacilityType,
    Gender,
)
from app.schemas.base_schema import BaseSchema, TimestampMixin, UUIDSchema

# ── Service Line Item ─────────────────────────────────────────────────────────


class ClaimServiceCreate(BaseSchema):
    service_code: str = Field(max_length=100)
    description: Optional[str] = None
    quantity: int = Field(ge=1)
    unit_price: float = Field(ge=0)
    total_price: float = Field(ge=0)


class ClaimServiceResponse(UUIDSchema):
    service_code: Optional[str] = None
    description: Optional[str] = None
    quantity: Optional[int] = None
    unit_price: Optional[float] = None
    total_price: Optional[float] = None
    is_upcoded: bool = False


# ── Provider (slim, for embedding in claim responses) ─────────────────────────


class ProviderSlim(BaseSchema):
    id: uuid.UUID
    sha_provider_code: str
    name: str
    county: Optional[str] = None
    facility_type: Optional[FacilityType] = None
    accreditation_status: Optional[AccreditationStatus] = None
    high_risk_flag: bool = False


class ProviderCreate(BaseSchema):
    sha_provider_code: str = Field(max_length=50)
    name: str
    county: Optional[str] = None
    sub_county: Optional[str] = None
    facility_type: Optional[FacilityType] = None
    accreditation_status: Optional[AccreditationStatus] = AccreditationStatus.ACTIVE
    phone: Optional[str] = None
    email: Optional[str] = None


class ProviderResponse(UUIDSchema, TimestampMixin):
    sha_provider_code: str
    name: str
    county: Optional[str] = None
    sub_county: Optional[str] = None
    facility_type: Optional[FacilityType] = None
    accreditation_status: Optional[AccreditationStatus] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    avg_claim_amount: Optional[float] = None
    peer_avg: Optional[float] = None
    high_risk_flag: bool = False


# ── Member (slim, for embedding in claim responses) ───────────────────────────


class MemberSlim(BaseSchema):
    id: uuid.UUID
    sha_member_id: str
    gender: Optional[Gender] = None
    county: Optional[str] = None
    coverage_status: Optional[str] = None


class MemberCreate(BaseSchema):
    sha_member_id: str = Field(max_length=50)
    national_id: Optional[str] = Field(None, max_length=20)
    gender: Optional[Gender] = None
    date_of_birth: Optional[date] = None
    county: Optional[str] = None
    coverage_status: Optional[str] = None
    scheme: Optional[str] = None


class MemberResponse(UUIDSchema, TimestampMixin):
    sha_member_id: str
    gender: Optional[Gender] = None
    county: Optional[str] = None
    coverage_status: Optional[str] = None
    scheme: Optional[str] = None
    # national_id and date_of_birth intentionally omitted — PII, masked from API


# ── Claim ─────────────────────────────────────────────────────────────────────


class ClaimCreate(BaseSchema):
    """
    Payload to ingest a new claim into the fraud detection system.
    Mirrors the SHA API claim submission format.
    """

    sha_claim_id: str = Field(
        max_length=100, description="SHA-issued claim reference number"
    )
    provider_code: str = Field(
        description="SHA provider code — used to resolve provider_id"
    )
    member_id_sha: str = Field(description="SHA member ID — used to resolve member_id")

    claim_type: Optional[ClaimType] = None
    sha_status: ClaimStatus = ClaimStatus.SUBMITTED

    admission_date: Optional[date] = None
    discharge_date: Optional[date] = None

    diagnosis_codes: List[str] = Field(default=[], description="ICD-10 codes")
    services: List[ClaimServiceCreate] = []

    total_claim_amount: Optional[float] = Field(None, ge=0)
    approved_amount: Optional[float] = Field(None, ge=0)

    submitted_at: Optional[datetime] = None
    raw_payload: Optional[Dict[str, Any]] = Field(
        None, description="Full original SHA payload stored verbatim"
    )

    @field_validator("diagnosis_codes")
    @classmethod
    def validate_icd_codes(cls, codes):
        # Basic format check — ICD-10 codes are alphanumeric, 3–7 chars
        for code in codes:
            if not 2 < len(code) <= 10:
                raise ValueError(f"Invalid ICD-10 code length: {code}")
        return codes


class ClaimStatusUpdate(BaseSchema):
    """Update the SHA status of a claim (e.g. APPROVED, REJECTED)."""

    sha_status: ClaimStatus
    approved_amount: Optional[float] = Field(None, ge=0)
    note: Optional[str] = None


class ClaimResponse(UUIDSchema, TimestampMixin):
    sha_claim_id: str
    claim_type: Optional[ClaimType] = None
    sha_status: ClaimStatus
    admission_date: Optional[date] = None
    discharge_date: Optional[date] = None
    diagnosis_codes: Optional[List[str]] = None
    total_claim_amount: Optional[float] = None
    approved_amount: Optional[float] = None
    submitted_at: Optional[datetime] = None
    processed_at: Optional[datetime] = None

    provider: Optional[ProviderSlim] = None
    member: Optional[MemberSlim] = None
    services: List[ClaimServiceResponse] = []


class ClaimDetailResponse(ClaimResponse):
    """Full claim with features and latest fraud score."""

    features: Optional["ClaimFeatureResponse"] = None
    latest_fraud_score: Optional["FraudScoreSlim"] = None


class ClaimListFilter(BaseSchema):
    """Query filters for GET /claims."""

    provider_id: Optional[uuid.UUID] = None
    member_id: Optional[uuid.UUID] = None
    sha_status: Optional[ClaimStatus] = None
    claim_type: Optional[ClaimType] = None
    submitted_from: Optional[datetime] = None
    submitted_to: Optional[datetime] = None
    min_amount: Optional[float] = None
    max_amount: Optional[float] = None


# ── Feature Response (defined here to avoid circular imports) ─────────────────


class ClaimFeatureResponse(BaseSchema):
    claim_id: uuid.UUID
    provider_avg_cost_90d: Optional[float] = None
    provider_cost_zscore: Optional[float] = None
    member_visits_30d: Optional[int] = None
    member_visits_7d: Optional[int] = None
    member_unique_providers_30d: Optional[int] = None
    duplicate_within_7d: Optional[bool] = None
    length_of_stay: Optional[int] = None
    weekend_submission: Optional[bool] = None
    diagnosis_cost_zscore: Optional[float] = None
    service_count: Optional[int] = None
    has_lab_without_diagnosis: Optional[bool] = None
    has_surgery_without_theatre: Optional[bool] = None
    engineered_at: Optional[datetime] = None


# ── Slim FraudScore (to embed in ClaimDetailResponse) ────────────────────────


class FraudScoreSlim(BaseSchema):
    id: uuid.UUID
    final_score: Optional[float] = None
    risk_level: Optional[str] = None
    scored_at: Optional[datetime] = None


# ── SHA Webhook Event ─────────────────────────────────────────────────────────


class SHAWebhookEvent(BaseSchema):
    """
    Payload SHA sends when a claim event occurs.
    Triggers our auto-ingest + score pipeline.
    """

    claim_id: str = Field(description="SHA claim reference ID")
    event: str = Field(description="e.g. CLAIM_SUBMITTED, CLAIM_UPDATED")
    provider_code: Optional[str] = None
    timestamp: Optional[datetime] = None
    claim_metadata: Optional[Dict[str, Any]] = None


class SHAWebhookResponse(BaseSchema):
    """What we return to SHA after processing a webhook."""

    claim_id: str
    received: bool = True
    fraud_score: Optional[float] = None
    risk_level: Optional[str] = None
    recommendation: Optional[str] = None


# Update forward refs
ClaimDetailResponse.model_rebuild()
