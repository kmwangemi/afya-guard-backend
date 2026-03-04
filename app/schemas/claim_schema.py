"""
SHA Fraud Detection — Claim Schemas

Shaped to match the Afya Guard frontend exactly:

  LIST VIEW  (claim.png)
    Columns:  Claim #  |  Provider  |  Patient ID (masked)  |
              Amount   |  Date      |  Risk Score pill      |  Status badge
    Filters:  search (claim # or provider name), status, risk_level, county,
              page, page_size

  DETAIL VIEW  (claim-single.png)
    Header:           sha_claim_id, provider_name, status pill, risk_score pill,
                      claim_amount, service_date
    Claim Information: patient_id_masked, provider_id, diagnosis, procedure,
                       service_date_range, county
    Fraud Analysis:    phantom_patient, upcoding, duplicate, provider_anomaly
                       — each with their own sub-fields
    Actions sidebar:   approve | reject | create_investigation | assign
    Details sidebar:   submitted, created, last_updated
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
    RiskLevel,
)
from app.schemas.base_schema import BaseSchema, TimestampMixin, UUIDSchema


# ── Service line item ─────────────────────────────────────────────────────────


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


# ── Provider ──────────────────────────────────────────────────────────────────


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


# ── Member ────────────────────────────────────────────────────────────────────


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


# ── Claim create / update ─────────────────────────────────────────────────────


class ClaimCreate(BaseSchema):
    """Payload to ingest a new claim. Mirrors the SHA API format."""

    sha_claim_id: str = Field(max_length=100)
    provider_code: str
    member_id_sha: str

    claim_type: Optional[ClaimType] = None
    sha_status: ClaimStatus = ClaimStatus.SUBMITTED

    admission_date: Optional[date] = None
    discharge_date: Optional[date] = None
    diagnosis_codes: List[str] = Field(default=[])
    services: List[ClaimServiceCreate] = []

    total_claim_amount: Optional[float] = Field(None, ge=0)
    approved_amount: Optional[float] = Field(None, ge=0)
    submitted_at: Optional[datetime] = None
    raw_payload: Optional[Dict[str, Any]] = None

    @field_validator("diagnosis_codes")
    @classmethod
    def validate_icd_codes(cls, codes):
        for code in codes:
            if not (2 < len(code) <= 10):
                raise ValueError(f"Invalid ICD-10 code length: {code}")
        return codes


class ClaimStatusUpdate(BaseSchema):
    sha_status: ClaimStatus
    approved_amount: Optional[float] = Field(None, ge=0)
    note: Optional[str] = None


# ══════════════════════════════════════════════════════════════════════════════
# LIST VIEW  —  claim.png
# ══════════════════════════════════════════════════════════════════════════════


class ClaimListFilter(BaseSchema):
    """
    Mirrors every control in the UI filter panel (claim.png).

    search     — ILIKE on sha_claim_id OR provider.name
    sha_status — maps to the Status dropdown
                 UI label → enum value:
                   "Pending"            → SUBMITTED
                   "Approved"           → APPROVED
                   "Rejected"           → REJECTED
                   "Flagged"            → FLAGGED
                   "Under Investigation"→ UNDER_REVIEW
                   "Paid"               → PAID
    risk_level — joined from the claim's latest FraudScore
    county     — ILIKE on provider.county
    """

    search: Optional[str] = Field(
        None, description="Search by claim number or provider name"
    )
    sha_status: Optional[ClaimStatus] = Field(
        None, description="Filter by claim status"
    )
    risk_level: Optional[RiskLevel] = Field(
        None, description="Filter by fraud risk level from latest score"
    )
    county: Optional[str] = Field(None, description="Filter by provider county")

    # Non-UI advanced filters
    provider_id: Optional[uuid.UUID] = None
    member_id: Optional[uuid.UUID] = None
    claim_type: Optional[ClaimType] = None
    submitted_from: Optional[datetime] = None
    submitted_to: Optional[datetime] = None
    min_amount: Optional[float] = None
    max_amount: Optional[float] = None


class ClaimListItem(BaseSchema):
    """
    One row in the claims table (claim.png).

    Claim # | Provider | Patient ID | Amount | Date | Risk Score | Status
    """

    id: uuid.UUID
    sha_claim_id: str  # "CLM-2024-000001"
    provider_name: Optional[str] = None  # "MP Shah Medical Centre"
    provider_id_code: Optional[str] = None  # "prov_005"

    # Patient ID masked to last-4, e.g. "****4559"
    member_sha_id_masked: Optional[str] = None

    total_claim_amount: Optional[float] = None  # 378000.0
    service_date: Optional[date] = None  # admission_date or submitted_at.date()

    # Risk pill — number drives colour on the frontend
    risk_score: Optional[float] = None  # 0–100
    risk_level: Optional[RiskLevel] = None

    # Status badge
    status: ClaimStatus


# ══════════════════════════════════════════════════════════════════════════════
# DETAIL VIEW  —  claim-single.png
# ══════════════════════════════════════════════════════════════════════════════

# ── Claim Information card ────────────────────────────────────────────────────


class ClaimInformation(BaseSchema):
    """
    Left-top card: Claim Information.
    Shown fields (claim-single.png):
      Patient ID (Masked) | Provider ID
      Diagnosis           | Procedure
      Service Date Range  | County
    """

    patient_id_masked: Optional[str] = None  # "****1697"
    provider_id_code: Optional[str] = None  # "prov_005"
    provider_name: Optional[str] = None  # "Coptic Hospital"

    diagnosis: Optional[str] = None  # "Hypertension, Type 2 Diabetes, ..."
    diagnosis_codes: List[str] = []  # raw ICD-10 codes

    procedure: Optional[str] = None  # "Consultation, Blood Test, X-Ray"

    service_date_from: Optional[date] = None  # admission_date
    service_date_to: Optional[date] = None  # discharge_date

    county: Optional[str] = None  # "Kisumu"


# ── Per-detector fraud analysis blocks ───────────────────────────────────────


class PhantomPatientAnalysis(BaseSchema):
    """
    'Phantom Patient Analysis' section (claim-single.png).
    Shows green check when passed, red when failed.
    Sub-fields: IPRS Status, Geographic Anomaly, Visit Frequency Anomaly.
    """

    detected: bool = False
    iprs_status: str = "UNVERIFIED"  # "VERIFIED" | "NOT_FOUND" | "UNVERIFIED"
    geographic_anomaly: bool = False  # "Yes" / "No"
    visit_frequency_anomaly: bool = False  # "Yes" / "No"
    confidence: float = 0.0  # 0–100, e.g. 82.0


class DuplicateClaimAnalysis(BaseSchema):
    """'Duplicate Claim' section in Fraud Analysis card."""

    detected: bool = False
    duplicate_count: int = 0
    duplicate_claim_ids: List[str] = []  # sha_claim_ids of the duplicates
    same_provider: bool = False
    window_days: int = 7
    confidence: float = 0.0


class UpcodingAnalysis(BaseSchema):
    """
    'Upcoding Analysis' section (claim-single.png).
    Sub-fields: Detected, Confidence (e.g. 14.9%).
    """

    detected: bool = False
    flagged_service_codes: List[str] = []
    flag_reasons: List[str] = []
    confidence: float = 0.0  # e.g. 14.9


class ProviderAnomalyAnalysis(BaseSchema):
    """'Provider Anomaly' section in Fraud Analysis card."""

    detected: bool = False
    provider_vs_peer_ratio: Optional[float] = None  # 2.3 = 2.3× peer avg
    high_risk_flag: bool = False
    confidence: float = 0.0


class FraudAnalysis(BaseSchema):
    """
    Full Fraud Analysis card — all four detector outputs.
    Each sub-object maps 1:1 to a collapsible section in the UI.
    """

    overall_score: Optional[float] = None  # final_score 0–100
    risk_level: Optional[RiskLevel] = None

    phantom_patient: PhantomPatientAnalysis = PhantomPatientAnalysis()
    duplicate_claim: DuplicateClaimAnalysis = DuplicateClaimAnalysis()
    upcoding: UpcodingAnalysis = UpcodingAnalysis()
    provider_anomaly: ProviderAnomalyAnalysis = ProviderAnomalyAnalysis()

    # Top human-readable flags for quick banner display
    top_flags: List[str] = []

    # Score breakdown visible to analysts
    rule_score: Optional[float] = None
    ml_score: Optional[float] = None
    detector_scores: Optional[Dict[str, float]] = None


# ── Details sidebar ───────────────────────────────────────────────────────────


class ClaimTimestamps(BaseSchema):
    """
    'Details' right sidebar (claim-single.png).
    Fields: Submitted | Created | Last Updated
    """

    submitted: Optional[datetime] = None  # claim.submitted_at
    created: Optional[datetime] = None  # claim.created_at
    last_updated: Optional[datetime] = None  # claim.updated_at


# ── Full detail response ──────────────────────────────────────────────────────


class ClaimDetailResponse(BaseSchema):
    """
    Full single-claim API response matching claim-single.png.

    Header         — sha_claim_id, provider_name, status, risk_score,
                     claim_amount, service_date
    Left top       — claim_information (Claim Information card)
    Left bottom    — fraud_analysis (Fraud Analysis card)
    Right top      — available_actions list → buttons
    Right bottom   — details (timestamps)
    """

    # Header
    id: uuid.UUID
    sha_claim_id: str  # "CLM-2024-000001"
    provider_name: Optional[str] = None  # "Coptic Hospital"

    status: ClaimStatus  # "Pending"
    risk_score: Optional[float] = None  # 0–100
    risk_level: Optional[RiskLevel] = None  # drives pill colour
    claim_amount: Optional[float] = None  # 67000.0
    service_date: Optional[date] = None  # "19 Feb 2024"

    # Cards
    claim_information: ClaimInformation = ClaimInformation()
    fraud_analysis: FraudAnalysis = FraudAnalysis()

    # Actions sidebar
    # Values: "approve" | "reject" | "create_investigation" | "assign"
    available_actions: List[str] = []

    # Details sidebar
    details: ClaimTimestamps = ClaimTimestamps()

    # Raw / extended fields
    claim_type: Optional[ClaimType] = None
    services: List[ClaimServiceResponse] = []
    fraud_score_id: Optional[uuid.UUID] = None


# ── Slim types used internally ────────────────────────────────────────────────


class FraudScoreSlim(BaseSchema):
    id: uuid.UUID
    final_score: Optional[float] = None
    risk_level: Optional[RiskLevel] = None
    scored_at: Optional[datetime] = None


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


# ── SHA Webhook ───────────────────────────────────────────────────────────────


class SHAWebhookEvent(BaseSchema):
    claim_id: str
    event: str
    provider_code: Optional[str] = None
    timestamp: Optional[datetime] = None
    metadata: Optional[Dict[str, Any]] = None


class SHAWebhookResponse(BaseSchema):
    claim_id: str
    received: bool = True
    fraud_score: Optional[float] = None
    risk_level: Optional[str] = None
    recommendation: Optional[str] = None


ClaimDetailResponse.model_rebuild()
