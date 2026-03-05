"""
SHA Fraud Detection — Alert Schemas

Shaped to match the Afya Guard frontend exactly:

  LIST VIEW  (Alert-main-page.png)
    Columns:  Alert Number  |  Type          |  Provider  |
              Status        |  Severity pill |  Fraud Amount  |  Created
    Filters:  search (alert number or provider name),
              severity level ("All Levels"),
              status ("All Statuses"),
              page_size

  DETAIL VIEW  (alert-details-page.png)
    Header:           ALERT-00120, subtitle "Alert: duplicate claim"
    Alert Summary card:
      Type            Severity pill
      Status          Created date
    Related Claim card:
      Claim Number (link)   Provider (link)
    Description card:
      Full alert message / description text
    Fraud Analysis card:
      Estimated Fraud Amount (highlighted, red text)
      Risk Score % (highlighted, yellow background)
    Quick Actions sidebar:
      Update Status dropdown (Closed / Open / Acknowledged etc.)
      Assign to Investigator button
    Assigned To sidebar:
      Analyst avatar, name, role
    Timeline sidebar:
      Alert Created — date
"""

import uuid
from datetime import datetime
from typing import Dict, List, Optional

from pydantic import Field

from app.models.enums_model import AlertSeverity, AlertStatus, AlertType
from app.schemas.base_schema import BaseSchema, UUIDSchema


# ══════════════════════════════════════════════════════════════════════════════
# WRITE SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════


class AlertStatusUpdate(BaseSchema):
    """PATCH /alerts/{id}/status — update lifecycle state."""

    status: AlertStatus
    note: Optional[str] = Field(
        None, description="Optional resolution or escalation note"
    )
    is_false_positive: Optional[bool] = None


class AlertAssignRequest(BaseSchema):
    """PATCH /alerts/{id}/assign — assign to an analyst."""

    user_id: uuid.UUID = Field(
        description="UUID of the analyst to assign this alert to"
    )


class AlertAcknowledgeRequest(BaseSchema):
    """PATCH /alerts/{id}/acknowledge."""

    note: Optional[str] = None


class AlertResolveRequest(BaseSchema):
    """PATCH /alerts/{id}/resolve."""

    resolution_note: str = Field(
        min_length=5, description="Required reason for resolution"
    )
    is_false_positive: bool = False


# ══════════════════════════════════════════════════════════════════════════════
# LIST VIEW  (Alert-main-page.png)
# ══════════════════════════════════════════════════════════════════════════════


class AlertListFilter(BaseSchema):
    """
    Every filter control in the alerts list UI (Alert-main-page.png).

    search    — ILIKE on alert_number OR provider name
    severity  — "All Levels" dropdown: INFO | WARNING | HIGH | CRITICAL
    status    — "All Statuses" dropdown: OPEN | ACKNOWLEDGED | INVESTIGATING |
                                         ESCALATED | RESOLVED | EXPIRED
    """

    search: Optional[str] = Field(
        None, description="Search by alert number or provider name"
    )
    severity: Optional[AlertSeverity] = Field(
        None, description="Filter by severity level"
    )
    status: Optional[AlertStatus] = Field(
        None, description="Filter by alert lifecycle status"
    )
    alert_type: Optional[AlertType] = Field(
        None, description="Filter by fraud signal type"
    )
    provider_id: Optional[uuid.UUID] = None
    assigned_to: Optional[uuid.UUID] = None
    raised_from: Optional[datetime] = None
    raised_to: Optional[datetime] = None


class AlertListItem(BaseSchema):
    """
    One row in the alerts table (Alert-main-page.png).

    Alert Number | Type | Provider | Status | Severity pill | Fraud Amount | Created
    """

    id: uuid.UUID

    # Alert Number — formatted display string, e.g. "ALERT-00120"
    alert_number: str

    # Type column — human-readable label mapped from AlertType enum
    # e.g. "Pattern Detected", "High Risk Claim", "Phantom Patient"
    type_display: str

    # Provider column — from the related claim's provider
    provider_name: Optional[str] = None
    provider_id: Optional[uuid.UUID] = None

    # Status badge (shown in detail but not as a column in the list screenshot,
    # kept here for filtering / frontend flexibility)
    status: AlertStatus

    # Severity pill — colour driven by value:
    #   CRITICAL → red, HIGH → orange, WARNING → green, INFO → green
    severity: AlertSeverity

    # Fraud Amount — claim's total_claim_amount (the financial exposure)
    fraud_amount: Optional[float] = None  # e.g. 396000.0 → "Ksh 396K"

    # Created timestamp
    created_at: datetime  # "3 Mar 2026, 22:51"

    # Extra for frontend convenience
    alert_type: AlertType
    claim_id: Optional[uuid.UUID] = None
    sha_claim_id: Optional[str] = None


# ══════════════════════════════════════════════════════════════════════════════
# DETAIL VIEW  (alert-details-page.png)
# ══════════════════════════════════════════════════════════════════════════════

# ── Alert Summary card ────────────────────────────────────────────────────────


class AlertSummary(BaseSchema):
    """
    'Alert Summary' left card (alert-details-page.png).

    Type            Severity pill
    Status          Created date
    """

    alert_type: AlertType  # raw enum
    type_display: str  # "Pattern Detected"
    severity: AlertSeverity
    status: AlertStatus
    created_at: datetime  # "3 Mar 2026"


# ── Related Claim card ────────────────────────────────────────────────────────


class RelatedClaim(BaseSchema):
    """
    'Related Claim' card (alert-details-page.png).

    Claim Number (link)   Provider (link)
    """

    claim_id: uuid.UUID
    sha_claim_id: str  # "CLM-003885"
    provider_id: Optional[uuid.UUID] = None
    provider_name: Optional[str] = None  # "Nairobi Central Hospital"


# ── Fraud Analysis card ───────────────────────────────────────────────────────


class AlertFraudAnalysis(BaseSchema):
    """
    'Fraud Analysis' card (alert-details-page.png).

    Two highlighted boxes:
      Estimated Fraud Amount  →  Ksh 396K   (red text, pink background)
      Risk Score              →  30%         (orange text, yellow background)
    """

    estimated_fraud_amount: Optional[float] = None  # claim's total_claim_amount
    risk_score_percentage: Optional[float] = None  # score_at_alert × 100 if 0–1,
    # or raw if already 0–100


# ── Assigned To sidebar ───────────────────────────────────────────────────────


class AssignedAnalyst(BaseSchema):
    """
    'Assigned To' sidebar card (alert-details-page.png).

    Avatar initial  |  Name  |  Role
    """

    user_id: uuid.UUID
    full_name: str  # "Ahmed Hassan"
    role: Optional[str] = None  # "Investigator"
    avatar_initial: str = ""  # first letter of full_name


# ── Timeline sidebar ──────────────────────────────────────────────────────────


class TimelineEvent(BaseSchema):
    """One entry in the Timeline sidebar."""

    label: str  # "Alert Created"
    timestamp: datetime  # "3 Mar 2026"
    note: Optional[str] = None


# ── Full detail response ──────────────────────────────────────────────────────


class AlertDetailResponse(BaseSchema):
    """
    Full alert detail response (alert-details-page.png).

    Header             — alert_number, subtitle
    Left top           — alert_summary card
    Left middle        — related_claim card
    Left middle-2      — description card (message text)
    Left bottom        — fraud_analysis card
    Right top          — quick_actions (available status transitions + assign button)
    Right middle       — assigned_to sidebar
    Right bottom       — timeline sidebar
    """

    id: uuid.UUID
    alert_number: str  # "ALERT-00120"
    subtitle: str  # "Alert: duplicate claim"

    # Cards
    alert_summary: AlertSummary
    related_claim: Optional[RelatedClaim] = None
    description: Optional[str] = None  # full message text
    fraud_analysis: AlertFraudAnalysis = AlertFraudAnalysis()

    # Quick Actions sidebar
    # Values the status can be updated to from the current state
    available_status_transitions: List[AlertStatus] = []

    # Assigned To sidebar (None if unassigned)
    assigned_to: Optional[AssignedAnalyst] = None

    # Timeline
    timeline: List[TimelineEvent] = []

    # Raw fields
    alert_type: AlertType
    severity: AlertSeverity
    status: AlertStatus
    fraud_case_id: Optional[uuid.UUID] = None
    metadata: Optional[Dict] = None
