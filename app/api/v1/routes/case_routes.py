"""
SHA Fraud Detection — Investigation Routes

GET    /api/v1/investigations                    List (search + filter + paginate)
POST   /api/v1/investigations                    Create investigation
GET    /api/v1/investigations/{id}               Full detail
PATCH  /api/v1/investigations/{id}/status        Update Status (header button)
PATCH  /api/v1/investigations/{id}/progress      Update Progress (header button)
PATCH  /api/v1/investigations/{id}/assign        Assign to analyst
POST   /api/v1/investigations/{id}/evidence      Upload evidence file metadata
POST   /api/v1/investigations/{id}/notes         Add analyst note
GET    /api/v1/investigations/{id}/notes         List notes
"""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import PaginationParams, get_db, require_permission
from app.models.enums_model import CasePriority, CaseStatus, RiskLevel
from app.models.user_model import User
from app.schemas.base_schema import PaginatedResponse
from app.schemas.case_schema import (
    CaseNoteCreate,
    CaseNoteResponse,
    EvidenceUpload,
    InvestigationAssignRequest,
    InvestigationCreate,
    InvestigationDetailResponse,
    InvestigationListFilter,
    InvestigationListItem,
    InvestigationProgressUpdate,
    InvestigationStatusUpdate,
)
from app.services.case_service import InvestigationService

router = APIRouter(tags=["Investigations"])


# ── List investigations ───────────────────────────────────────────────────────


@router.get(
    "/investigations",
    response_model=PaginatedResponse[InvestigationListItem],
    summary="List investigations",
    description="""
Returns a paginated list matching the investigations list page.

**Columns:** INV number | Investigator | Provider | Status | Priority pill | Progress bar + % | Created

**Filters:**
- `search` — INV number, claim number, or provider name
- `status` — `OPEN` | `UNDER_REVIEW` | `CONFIRMED_FRAUD` | `CLEARED` | `CLOSED`
- `priority` — `LOW` | `MEDIUM` | `HIGH` | `URGENT`

**Priority pill colours** (driven by `priority` value):
- `URGENT` → red, `HIGH` → orange, `MEDIUM` → yellow, `LOW` → purple/blue
""",
)
async def list_investigations(
    search: Optional[str] = Query(
        None, description="Search case #, claim #, or provider"
    ),
    status: Optional[str] = Query(None, description="Filter by status"),
    priority: Optional[str] = Query(None, description="Filter by priority"),
    risk_level: Optional[str] = Query(None),
    assigned_to: Optional[uuid.UUID] = Query(None),
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("view_score")),
):
    try:
        status_f = CaseStatus(status) if status else None
    except ValueError:
        raise HTTPException(
            422, f"Invalid status '{status}'. Valid: {[s.value for s in CaseStatus]}"
        )

    try:
        priority_f = CasePriority(priority) if priority else None
    except ValueError:
        raise HTTPException(
            422,
            f"Invalid priority '{priority}'. Valid: {[p.value for p in CasePriority]}",
        )

    try:
        risk_f = RiskLevel(risk_level) if risk_level else None
    except ValueError:
        raise HTTPException(
            422,
            f"Invalid risk_level '{risk_level}'. Valid: {[r.value for r in RiskLevel]}",
        )

    filters = InvestigationListFilter(
        search=search,
        status=status_f,
        priority=priority_f,
        risk_level=risk_f,
        assigned_to=assigned_to,
    )

    items, total = await InvestigationService.list_investigations(
        db, filters, offset=pagination.offset, limit=pagination.page_size
    )

    return PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=-(-total // pagination.page_size) if total else 0,
    )


# ── Create investigation ──────────────────────────────────────────────────────


@router.post(
    "/investigations",
    response_model=InvestigationDetailResponse,
    status_code=201,
    summary="Create investigation",
    description="Open a new fraud investigation. Returns 409 if one already exists for the claim.",
)
async def create_investigation(
    data: InvestigationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("create_case")),
):
    return await InvestigationService.create(db, data, created_by=current_user)


# ── Investigation detail ──────────────────────────────────────────────────────


@router.get(
    "/investigations/{case_id}",
    response_model=InvestigationDetailResponse,
    summary="Get investigation detail",
    description="""
Returns the full investigation detail view (INV-XXXXX page).

- **stat_cards** — Status | Priority pill | Days Open | Progress bar
- **investigation_details** — Investigator, Related Claim, Created, Target Date
- **findings** — analyst findings narrative text
- **timeline** — ordered events: Alert created → Investigation opened → status changes → notes
- **evidence** — uploaded files table (File Name | Type | Uploaded By | Date)
- **summary** — right sidebar (Alert Number, Claim Number, Provider, Investigator)
- **quick_actions** — available_status_transitions, can_close, can_update_progress
""",
)
async def get_investigation(
    case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("view_score")),
):
    return await InvestigationService.get_detail(db, case_id)


# ── Update Status (header button) ────────────────────────────────────────────


@router.patch(
    "/investigations/{case_id}/status",
    response_model=InvestigationDetailResponse,
    summary="Update investigation status",
    description="""
Update lifecycle status — maps to the 'Update Status' header button.

Valid transitions:
- `OPEN`          → UNDER_REVIEW | CONFIRMED_FRAUD | CLEARED | CLOSED
- `UNDER_REVIEW`  → CONFIRMED_FRAUD | CLEARED | CLOSED
- `CONFIRMED_FRAUD`→ CLOSED
- `CLEARED`       → CLOSED
- `CLOSED`        → (terminal)

`resolution_summary` required for terminal states. Progress auto-sets to 100% on close.
""",
)
async def update_status(
    case_id: uuid.UUID,
    data: InvestigationStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("update_case")),
):
    return await InvestigationService.update_status(
        db, case_id, data, updated_by=current_user
    )


# ── Update Progress (header button) ──────────────────────────────────────────


@router.patch(
    "/investigations/{case_id}/progress",
    response_model=InvestigationDetailResponse,
    summary="Update investigation progress",
    description="Update progress % (0–100) and optionally update the Findings text. Maps to 'Update Progress' header button.",
)
async def update_progress(
    case_id: uuid.UUID,
    data: InvestigationProgressUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("update_case")),
):
    return await InvestigationService.update_progress(
        db, case_id, data, updated_by=current_user
    )


# ── Assign analyst ────────────────────────────────────────────────────────────


@router.patch(
    "/investigations/{case_id}/assign",
    response_model=InvestigationDetailResponse,
    summary="Assign investigation to analyst",
    description="Assign to an analyst. Auto-advances OPEN → UNDER_REVIEW on first assignment.",
)
async def assign_investigation(
    case_id: uuid.UUID,
    data: InvestigationAssignRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("assign_case")),
):
    return await InvestigationService.assign(
        db, case_id, data, assigned_by=current_user
    )


# ── Upload evidence (Evidence table Upload button) ────────────────────────────


@router.post(
    "/investigations/{case_id}/evidence",
    response_model=InvestigationDetailResponse,
    status_code=201,
    summary="Upload evidence file",
    description="""
Add evidence file metadata to the investigation Evidence table.

Send the file itself to your storage service first, then POST the metadata here:
```json
{
  "file_name": "claim_analysis.pdf",
  "file_type": "pdf",
  "file_url": "https://storage.example.com/evidence/claim_analysis.pdf"
}
```
""",
)
async def upload_evidence(
    case_id: uuid.UUID,
    data: EvidenceUpload,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("update_case")),
):
    return await InvestigationService.upload_evidence(
        db, case_id, data, uploaded_by=current_user
    )


# ── Notes ─────────────────────────────────────────────────────────────────────


@router.post(
    "/investigations/{case_id}/notes",
    response_model=CaseNoteResponse,
    status_code=201,
    summary="Add analyst note",
)
async def add_note(
    case_id: uuid.UUID,
    data: CaseNoteCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("create_case")),
):
    return await InvestigationService.add_note(
        db, case_id, data, created_by=current_user
    )


@router.get(
    "/investigations/{case_id}/notes",
    response_model=List[CaseNoteResponse],
    summary="List investigation notes",
)
async def get_notes(
    case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("view_score")),
):
    detail = await InvestigationService.get_detail(db, case_id)
    return detail.notes
