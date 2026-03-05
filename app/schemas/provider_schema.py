"""
SHA Fraud Detection — Provider Schemas

Shaped to match the Afya Guard frontend exactly:

  LIST VIEW  (Providers_page.png)
    Columns:  Provider (name + code)  |  Facility Type  |  County  |
              Total Claims            |  Flagged %      |  Risk Score pill
    Filters:  search (name or code), county, facility_type, risk_level,
              page, page_size

  DETAIL VIEW  (Provider_single_page.png)
    Header stats:  Risk Score pill  |  Total Claims  |  Flagged Claims %  |  Confirmed Fraud count
    Provider Information card:
      Facility Type  |  County
      Contact (phone)|  Email
      Bed Capacity   |  Status (accreditation)
    Risk Profile card:
      Claim Deviation %   (progress bar)
      Rejection Rate %    (progress bar)
      Fraud History Score (progress bar)
    Quick Stats sidebar:
      Total Claims  |  Flagged  |  Confirmed Fraud  |  Last Claim date
    Fraud History sidebar:
      Confirmed Cases  |  Suspected Cases  |  Total Amount
    Statistics card:
      Total Amount  |  Average Claim  |  Rejection Rate  |  Avg Processing Time (days)
"""

import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import Field

from app.models.enums_model import AccreditationStatus, FacilityType, RiskLevel
from app.schemas.base_schema import BaseSchema, TimestampMixin, UUIDSchema


# ══════════════════════════════════════════════════════════════════════════════
# WRITE SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════


class ProviderCreate(BaseSchema):
    sha_provider_code: str = Field(
        max_length=50, description="SHA-assigned provider code, e.g. NHF-00001"
    )
    name: str
    county: Optional[str] = None
    sub_county: Optional[str] = None
    facility_type: Optional[FacilityType] = None
    accreditation_status: Optional[AccreditationStatus] = AccreditationStatus.ACTIVE
    phone: Optional[str] = Field(None, max_length=20)
    email: Optional[str] = Field(None, max_length=255)
    bed_capacity: Optional[int] = Field(
        None, ge=0, description="Number of beds (inpatient facilities)"
    )


class ProviderUpdate(BaseSchema):
    name: Optional[str] = None
    county: Optional[str] = None
    sub_county: Optional[str] = None
    facility_type: Optional[FacilityType] = None
    accreditation_status: Optional[AccreditationStatus] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    bed_capacity: Optional[int] = Field(None, ge=0)
    high_risk_flag: Optional[bool] = None


# ══════════════════════════════════════════════════════════════════════════════
# LIST VIEW  (Providers_page.png)
# ══════════════════════════════════════════════════════════════════════════════


class ProviderListFilter(BaseSchema):
    """
    Every filter control in the provider list UI (Providers_page.png).

    search        — ILIKE on provider name OR sha_provider_code
    county        — dropdown filter, ILIKE match
    facility_type — "All Types" dropdown
    risk_level    — "All Levels" dropdown, derived from provider's avg fraud score
    """

    search: Optional[str] = Field(
        None, description="Search by provider name or code (e.g. 'NHF-00001')"
    )
    county: Optional[str] = Field(None, description="Filter by county")
    facility_type: Optional[FacilityType] = Field(
        None, description="Filter by facility type"
    )
    risk_level: Optional[RiskLevel] = Field(
        None, description="Filter by overall risk level derived from fraud scoring"
    )


class ProviderListItem(BaseSchema):
    """
    One row in the providers table (Providers_page.png).

    Provider (name + code) | Facility Type | County | Total Claims | Flagged % | Risk Score
    """

    id: uuid.UUID
    sha_provider_code: str  # "NHF-00001"
    name: str  # "MP Shah Medical Centre"

    facility_type: Optional[FacilityType] = None  # "Clinic"
    county: Optional[str] = None  # "Mombasa"

    # Total Claims — count of all claims for this provider
    total_claims: int = 0

    # Flagged % — percentage of claims that are FLAGGED or UNDER_REVIEW
    flagged_percentage: float = 0.0  # e.g. 0.9  → displayed as "0.9%"

    # Risk Score pill — colour-coded oval; driven by avg fraud score
    risk_score: Optional[float] = None  # 0–100
    risk_level: Optional[RiskLevel] = None  # LOW | MEDIUM | HIGH | CRITICAL

    # Status (for filtering but not a column in the table)
    accreditation_status: Optional[AccreditationStatus] = None
    high_risk_flag: bool = False


# ══════════════════════════════════════════════════════════════════════════════
# DETAIL VIEW  (Provider_single_page.png)
# ══════════════════════════════════════════════════════════════════════════════

# ── Header stat cards ─────────────────────────────────────────────────────────


class ProviderHeaderStats(BaseSchema):
    """
    Four stat cards across the top of the provider detail page.

    Risk Score pill  |  Total Claims  |  Flagged Claims %  |  Confirmed Fraud count
    """

    risk_score: Optional[float] = None  # 0–100 (drives pill colour)
    risk_level: Optional[RiskLevel] = None
    total_claims: int = 0  # "3,567"
    flagged_claims_percentage: float = 0.0  # "14.5%"
    confirmed_fraud_count: int = 0  # "29"


# ── Provider Information card ─────────────────────────────────────────────────


class ProviderInformation(BaseSchema):
    """
    'Provider Information' card (left, Provider_single_page.png).

    Facility Type  County
    Contact        Email
    Bed Capacity   Status
    """

    facility_type: Optional[FacilityType] = None  # "Clinic"
    county: Optional[str] = None  # "Mombasa"
    phone: Optional[str] = None  # "+254712345678"
    email: Optional[str] = None  # "info@provider1.ke"
    bed_capacity: Optional[int] = None  # 500
    status: Optional[AccreditationStatus] = None  # "Active" (ACTIVE)


# ── Risk Profile card ─────────────────────────────────────────────────────────


class RiskProfileBar(BaseSchema):
    """One progress bar in the Risk Profile card."""

    label: str  # "Claim Deviation"
    value: float  # 0–100 percentage, e.g. 58.0
    colour: str  # "red" | "orange" | "purple" | "green"


class RiskProfile(BaseSchema):
    """
    'Risk Profile' card — three labelled progress bars.

    Claim Deviation    58%   (red bar)
    Rejection Rate     27.0% (orange bar)
    Fraud History Score 32%  (purple bar)
    """

    claim_deviation: RiskProfileBar = RiskProfileBar(
        label="Claim Deviation", value=0.0, colour="red"
    )
    rejection_rate: RiskProfileBar = RiskProfileBar(
        label="Rejection Rate", value=0.0, colour="orange"
    )
    fraud_history_score: RiskProfileBar = RiskProfileBar(
        label="Fraud History Score", value=0.0, colour="purple"
    )


# ── Quick Stats sidebar ───────────────────────────────────────────────────────


class QuickStats(BaseSchema):
    """
    'Quick Stats' right sidebar card.

    Total Claims  3567
    Flagged       39
    Confirmed Fraud 29
    Last Claim    24 Feb 2026
    """

    total_claims: int = 0
    flagged: int = 0
    confirmed_fraud: int = 0
    last_claim_date: Optional[date] = None  # "24 Feb 2026"


# ── Fraud History sidebar ─────────────────────────────────────────────────────


class FraudHistory(BaseSchema):
    """
    'Fraud History' right sidebar card.

    Confirmed Cases   0
    Suspected Cases   0
    Total Amount      Ksh 4M
    """

    confirmed_cases: int = 0
    suspected_cases: int = 0
    total_fraud_amount: float = 0.0  # total KES amount across confirmed cases


# ── Statistics card ───────────────────────────────────────────────────────────


class ProviderStatistics(BaseSchema):
    """
    'Statistics' card — four figures in a 2×2 grid.

    Total Amount       Ksh 353M
    Average Claim      Ksh 248K
    Rejection Rate     10.4%
    Avg Processing Time 34 days
    """

    total_amount: float = 0.0  # sum of all claim amounts (KES)
    average_claim: float = 0.0  # avg claim amount (KES)
    rejection_rate: float = 0.0  # % claims REJECTED
    avg_processing_time_days: float = 0.0  # avg days from submitted_at → processed_at


# ── Full detail response ──────────────────────────────────────────────────────


class ProviderDetailResponse(BaseSchema):
    """
    Full provider detail response (Provider_single_page.png).

    Header stats       — risk_score, total_claims, flagged %, confirmed fraud
    Left top           — provider_information card
    Left middle        — risk_profile card (3 progress bars)
    Left bottom        — statistics card (4 figures)
    Right top          — quick_stats sidebar
    Right bottom       — fraud_history sidebar
    """

    id: uuid.UUID
    sha_provider_code: str  # "NHF-00001"
    name: str  # "MP Shah Medical Centre"

    # Header stats
    header: ProviderHeaderStats = ProviderHeaderStats()

    # Cards
    provider_information: ProviderInformation = ProviderInformation()
    risk_profile: RiskProfile = RiskProfile()
    statistics: ProviderStatistics = ProviderStatistics()

    # Sidebars
    quick_stats: QuickStats = QuickStats()
    fraud_history: FraudHistory = FraudHistory()

    # Raw fields (for advanced / API consumers)
    sub_county: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ── Simple provider response (used in embedded contexts) ──────────────────────


class ProviderResponse(UUIDSchema, TimestampMixin):
    """Flat provider response — used when embedding in claim/case responses."""

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
