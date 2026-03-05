"""
SHA Fraud Detection — Investigation Schemas

Shaped to match investigation UI screenshots exactly:

  LIST PAGE  (investigation-page.png)
    Header:  "Investigations" | + Create Investigation button
    Filters: Search case number | All Statuses | All Priorities | 25 per page
    Columns: Case Number (INV-XXXXX)  |  Investigator  |  Provider  |
             Status  |  Priority pill  |  Progress bar + %  |  Created  |  Actions

  DETAIL PAGE  (investigation_single_page_1.png + _2.png)
    Header:  INV-00084  |  Nakuru Clinic (subtitle)  |  Update Status  |  Update Progress
    4 stat cards:  Status  |  Priority pill  |  Days Open  |  Progress bar
    Left — Investigation Details card:
      Investigator  |  Related Claim
      Created       |  Target Date
    Left — Findings card (text block)
    Left — Timeline card (purple dot, event name, actor, note, timestamp)
    Left — Evidence card (File Name | Type | Uploaded By | Date) + Upload button
    Right — Summary sidebar:
      Alert Number  |  Claim Number  |  Provider  |  Investigator
"""

import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import Field

from app.models.enums_model import CasePriority, CaseStatus, RiskLevel
from app.schemas.base_schema import BaseSchema, UUIDSchema


# ══════════════════════════════════════════════════════════════════════════════
# WRITE SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════


class InvestigationCreate(BaseSchema):
    """Open a new investigation — maps to the 'Create Investigation' dialog."""

    claim_id: uuid.UUID
    fraud_score_id: uuid.UUID
    priority: CasePriority = CasePriority.MEDIUM
    assigned_to: Optional[uuid.UUID] = None
    target_date: Optional[date] = None
    notes: Optional[str] = Field(None, description="Initial opening notes")


class InvestigationStatusUpdate(BaseSchema):
    """'Update Status' dialog on the detail page."""

    status: CaseStatus
    resolution_summary: Optional[str] = Field(
        None,
        description="Required when status is CONFIRMED_FRAUD, CLEARED, or CLOSED",
    )
    estimated_loss: Optional[float] = Field(None, ge=0)


class InvestigationProgressUpdate(BaseSchema):
    """'Update Progress' dialog on the detail page."""

    progress: int = Field(ge=0, le=100, description="Completion percentage 0–100")
    findings: Optional[str] = Field(None, description="Analyst findings narrative")


class InvestigationAssignRequest(BaseSchema):
    assigned_to: uuid.UUID


class InvestigationPriorityUpdate(BaseSchema):
    priority: CasePriority


class EvidenceUpload(BaseSchema):
    """Metadata for an evidence file upload."""

    file_name: str
    file_type: str = Field(description="e.g. 'pdf', 'xlsx', 'jpg'")
    file_url: str = Field(description="Storage URL of the uploaded file")


class CaseNoteCreate(BaseSchema):
    note: str = Field(min_length=5)
    attachments: Optional[List[Dict[str, Any]]] = None


# ══════════════════════════════════════════════════════════════════════════════
# LIST VIEW  (investigation-page.png)
# ══════════════════════════════════════════════════════════════════════════════


class InvestigationListFilter(BaseSchema):
    """Filter controls on the investigations list page."""

    search: Optional[str] = Field(
        None, description="Search by INV number, claim number, or provider name"
    )
    status: Optional[CaseStatus] = None  # "All Statuses" dropdown
    priority: Optional[CasePriority] = None  # "All Priorities" dropdown
    risk_level: Optional[RiskLevel] = None
    assigned_to: Optional[uuid.UUID] = None
    opened_from: Optional[datetime] = None
    opened_to: Optional[datetime] = None


class InvestigationListItem(BaseSchema):
    """
    One row in the investigations table (investigation-page.png).

    Case Number (INV-XXXXX) | Investigator | Provider | Status |
    Priority pill | Progress bar + % | Created | Actions (View)
    """

    id: uuid.UUID
    inv_number: str  # "INV-00084"

    # Investigator column
    investigator_name: Optional[str] = None
    investigator_id: Optional[uuid.UUID] = None

    # Provider column
    provider_name: Optional[str] = None
    provider_id: Optional[uuid.UUID] = None

    # Claim (for linking)
    claim_id: uuid.UUID
    sha_claim_id: Optional[str] = None

    # Status & Priority
    status: CaseStatus
    priority: CasePriority

    # Progress bar
    progress: int = 0  # 0–100

    # Created timestamp
    opened_at: datetime  # "4 Mar 2026, 08:38"
    closed_at: Optional[datetime] = None

    # Extra (not shown in table but useful for the frontend)
    risk_level: Optional[RiskLevel] = None
    final_score: Optional[float] = None
    note_count: int = 0


# ══════════════════════════════════════════════════════════════════════════════
# DETAIL VIEW  (investigation_single_page_1.png + _2.png)
# ══════════════════════════════════════════════════════════════════════════════

# ── 4 Stat cards ─────────────────────────────────────────────────────────────


class InvestigationStatCards(BaseSchema):
    """
    Four cards across the top of the detail page.

    Status  |  Priority pill  |  Days Open (int)  |  Progress bar + %
    """

    status: CaseStatus  # Status card
    priority: CasePriority  # Priority card (pill)
    days_open: int  # Days Open card  e.g. 53
    progress: int  # Progress card  0–100


# ── Investigation Details card (left top) ─────────────────────────────────────


class InvestigationDetails(BaseSchema):
    """
    'Investigation Details' card (two-column layout):

    Investigator   |  Related Claim (sha_claim_id)
    Created        |  Target Date
    """

    investigator_name: Optional[str] = None  # "Maria Garcia"
    investigator_id: Optional[uuid.UUID] = None
    related_claim: Optional[str] = None  # "CLM-006880"
    claim_id: Optional[uuid.UUID] = None
    created_at: datetime
    target_date: Optional[date] = None  # "19 Mar 2026"
    closed_at: Optional[datetime] = None


# ── Evidence row ──────────────────────────────────────────────────────────────


class EvidenceFile(BaseSchema):
    """One row in the Evidence table."""

    id: str  # UUID string
    file_name: str  # "claim_analysis.pdf"
    file_type: str  # "PDF"
    file_url: Optional[str] = None
    uploaded_by: Optional[str] = None  # "Maria Garcia"
    uploaded_at: Optional[datetime] = None  # "5 Mar 2026"


# ── Timeline event ────────────────────────────────────────────────────────────


class TimelineEvent(BaseSchema):
    """
    One entry in the Timeline (purple dot, bold title, actor, note, timestamp).

    e.g.:
      ● Alert created     System      Automatic alert generated    4 Mar 2026, 08:38
      ● Investigation opened  John Omondi  Case assigned for investigation  5 Mar 2026, 08:38
    """

    event: str  # "Alert created" / "Investigation opened"
    actor: Optional[str] = None  # "System" / "John Omondi"
    note: Optional[str] = None  # "Automatic alert generated"
    timestamp: datetime


# ── Summary sidebar ───────────────────────────────────────────────────────────


class InvestigationSummary(BaseSchema):
    """
    'Summary' right sidebar card (investigation_single_page_1.png).

    Alert Number   ALERT-00319
    Claim Number   CLM-006880
    Provider       Nakuru Clinic
    Investigator   Maria Garcia
    """

    alert_number: Optional[str] = None  # "ALERT-00319"
    alert_id: Optional[uuid.UUID] = None
    claim_number: Optional[str] = None  # "CLM-006880"
    claim_id: Optional[uuid.UUID] = None
    provider_name: Optional[str] = None  # "Nakuru Clinic"
    provider_id: Optional[uuid.UUID] = None
    investigator_name: Optional[str] = None  # "Maria Garcia"
    investigator_id: Optional[uuid.UUID] = None


# ── Note response ─────────────────────────────────────────────────────────────


class CaseNoteResponse(UUIDSchema):
    case_id: uuid.UUID
    note: str
    attachments: Optional[List[Dict[str, Any]]] = None
    created_at: datetime
    author_name: Optional[str] = None
    author_id: Optional[uuid.UUID] = None


# ── Quick actions sidebar ─────────────────────────────────────────────────────


class InvestigationQuickActions(BaseSchema):
    """
    Drives the 'Update Status' and 'Update Progress' buttons in the header.
    Also used to populate the Update Status dropdown options.
    """

    available_status_transitions: List[CaseStatus] = []
    can_close: bool = True  # Show 'Close Investigation' button
    can_update_progress: bool = True
    can_assign: bool = True
    can_upload_evidence: bool = True


# ── Full detail response ──────────────────────────────────────────────────────


class InvestigationDetailResponse(BaseSchema):
    """
    Full investigation detail response (both screenshot pages combined).

    id / inv_number      — header (INV-00084)
    subtitle             — provider name shown under the number
    stat_cards           — Status | Priority | Days Open | Progress
    investigation_details— left card (investigator, claim, created, target date)
    findings             — text block ("Pattern analysis reveals...")
    timeline             — ordered events with purple dots
    evidence             — evidence file table rows
    summary              — right sidebar (alert #, claim #, provider, investigator)
    quick_actions        — available transitions for Update Status dialog
    notes                — analyst notes (for API consumers)
    """

    id: uuid.UUID
    inv_number: str  # "INV-00084"
    subtitle: str  # provider name "Nakuru Clinic"

    # 4 stat cards
    stat_cards: InvestigationStatCards

    # Left column
    investigation_details: InvestigationDetails
    findings: Optional[str] = None  # Findings card text
    timeline: List[TimelineEvent] = []
    evidence: List[EvidenceFile] = []

    # Right sidebar
    summary: InvestigationSummary

    # Quick Actions (header buttons)
    quick_actions: InvestigationQuickActions = InvestigationQuickActions()

    # Notes (for API / note panel)
    notes: List[CaseNoteResponse] = []

    # Raw fields
    status: CaseStatus
    priority: CasePriority
    progress: int
    opened_at: datetime
    closed_at: Optional[datetime] = None
    claim_id: uuid.UUID
    fraud_score_id: uuid.UUID
    assigned_analyst_id: Optional[uuid.UUID] = None
    resolution_summary: Optional[str] = None
    estimated_loss: Optional[float] = None
