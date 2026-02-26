"""
SHA Fraud Detection — Case Management Schemas

Covers: case creation, assignment, status transitions, notes, resolution.
"""

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import Field

from app.models.enums_model import CasePriority, CaseStatus, RiskLevel
from app.schemas.base_schema import BaseSchema, TimestampMixin, UUIDSchema

# ── Case Note ─────────────────────────────────────────────────────────────────


class CaseNoteCreate(BaseSchema):
    note: str = Field(min_length=5)
    attachments: Optional[List[dict]] = None


class CaseNoteResponse(UUIDSchema):
    case_id: uuid.UUID
    note: str
    attachments: Optional[List[dict]] = None
    created_at: datetime
    author_name: Optional[str] = None  # resolved from created_by user


# ── Fraud Case ────────────────────────────────────────────────────────────────


class FraudCaseCreate(BaseSchema):
    """Manually open a case (system auto-opens for HIGH/CRITICAL scores)."""

    claim_id: uuid.UUID
    fraud_score_id: uuid.UUID
    priority: CasePriority = CasePriority.MEDIUM
    assigned_to: Optional[uuid.UUID] = None


class CaseAssignRequest(BaseSchema):
    assigned_to: uuid.UUID


class CaseStatusUpdate(BaseSchema):
    status: CaseStatus
    resolution_summary: Optional[str] = Field(
        None, description="Required when status is CONFIRMED_FRAUD or CLEARED"
    )
    estimated_loss: Optional[float] = Field(None, ge=0)


class CasePriorityUpdate(BaseSchema):
    priority: CasePriority


class FraudCaseResponse(UUIDSchema, TimestampMixin):
    claim_id: uuid.UUID
    fraud_score_id: uuid.UUID
    status: CaseStatus
    priority: CasePriority
    assigned_to: Optional[uuid.UUID] = None
    assigned_analyst_name: Optional[str] = None
    resolution_summary: Optional[str] = None
    estimated_loss: Optional[float] = None
    opened_at: datetime
    closed_at: Optional[datetime] = None
    notes: List[CaseNoteResponse] = []

    # Denormalised from linked claim / score for quick display
    sha_claim_id: Optional[str] = None
    provider_name: Optional[str] = None
    final_score: Optional[float] = None
    risk_level: Optional[RiskLevel] = None


class FraudCaseListResponse(BaseSchema):
    """Slim version for list view."""

    id: uuid.UUID
    claim_id: uuid.UUID
    sha_claim_id: Optional[str] = None
    status: CaseStatus
    priority: CasePriority
    risk_level: Optional[RiskLevel] = None
    final_score: Optional[float] = None
    provider_name: Optional[str] = None
    assigned_analyst_name: Optional[str] = None
    opened_at: datetime
    closed_at: Optional[datetime] = None
    note_count: int = 0


class CaseListFilter(BaseSchema):
    """Query filters for GET /cases."""

    status: Optional[CaseStatus] = None
    priority: Optional[CasePriority] = None
    assigned_to: Optional[uuid.UUID] = None
    risk_level: Optional[RiskLevel] = None
    opened_from: Optional[datetime] = None
    opened_to: Optional[datetime] = None
