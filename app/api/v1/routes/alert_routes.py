"""
SHA Fraud Detection — Alert Routes

GET    /api/v1/alerts                        List alerts (search + filter + paginate)
GET    /api/v1/alerts/{id}                   Full alert detail (alert-details-page.png)
PATCH  /api/v1/alerts/{id}/status            Update lifecycle status
PATCH  /api/v1/alerts/{id}/acknowledge       Quick-acknowledge an alert
PATCH  /api/v1/alerts/{id}/resolve           Resolve or dismiss an alert
PATCH  /api/v1/alerts/{id}/assign            Assign to an investigator
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import PaginationParams, get_db, require_permission
from app.models.enums_model import AlertSeverity, AlertStatus, AlertType
from app.models.user_model import User
from app.schemas.alert_schema import (
    AlertAcknowledgeRequest,
    AlertAssignRequest,
    AlertDetailResponse,
    AlertListFilter,
    AlertListItem,
    AlertResolveRequest,
    AlertStatusUpdate,
)
from app.schemas.base_schema import PaginatedResponse
from app.services.alert_service import AlertService

router = APIRouter(tags=["Alerts"])


# ── List alerts ───────────────────────────────────────────────────────────────


@router.get(
    "/alerts",
    response_model=PaginatedResponse[AlertListItem],
    summary="List fraud alerts",
    description="""
Returns a paginated list of alerts matching the Alerts page (Alert-main-page.png).

Each row: Alert Number | Type | Provider | Status | Severity pill | Fraud Amount | Created

**Filters:**
- `search` — partial match on provider name or alert title
- `severity` — `INFO` | `WARNING` | `HIGH` | `CRITICAL`  (maps to "All Levels" dropdown)
- `status` — `OPEN` | `ACKNOWLEDGED` | `INVESTIGATING` | `ESCALATED` | `RESOLVED` | `EXPIRED`
- `alert_type` — specific fraud signal type

**Severity → pill colour:**
- `CRITICAL` → red
- `HIGH` → orange
- `WARNING` → green
- `INFO` → green (lighter)

**Pagination:** `page` (default 1), `page_size` (default 25).
""",
)
async def list_alerts(
    search: Optional[str] = Query(
        None, description="Search by provider name or alert title"
    ),
    severity: Optional[str] = Query(
        None,
        description="Filter by severity: INFO | WARNING | HIGH | CRITICAL",
        examples="HIGH",
    ),
    status: Optional[str] = Query(
        None,
        description="Filter by status: OPEN | ACKNOWLEDGED | INVESTIGATING | ESCALATED | RESOLVED | EXPIRED",
        examples="OPEN",
    ),
    alert_type: Optional[str] = Query(None, description="Filter by alert type"),
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("view_score")),
):
    try:
        sev_filter = AlertSeverity(severity) if severity else None
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid severity '{severity}'. Valid: {[s.value for s in AlertSeverity]}",
        )

    try:
        status_filter = AlertStatus(status) if status else None
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status '{status}'. Valid: {[s.value for s in AlertStatus]}",
        )

    try:
        type_filter = AlertType(alert_type) if alert_type else None
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid alert_type '{alert_type}'. Valid: {[t.value for t in AlertType]}",
        )

    filters = AlertListFilter(
        search=search,
        severity=sev_filter,
        status=status_filter,
        alert_type=type_filter,
    )

    items, total = await AlertService.list_alerts(
        db, filters, offset=pagination.offset, limit=pagination.page_size
    )

    return PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=-(-total // pagination.page_size) if total else 0,
    )


# ── Alert detail ──────────────────────────────────────────────────────────────


@router.get(
    "/alerts/{alert_id}",
    response_model=AlertDetailResponse,
    summary="Get full alert detail",
    description="""
Returns the full alert detail view (alert-details-page.png).

- **Alert Summary**: Type, Severity pill, Status, Created date
- **Related Claim**: Claim number (link) + Provider (link)
- **Description**: Full alert message text
- **Fraud Analysis**: Estimated Fraud Amount (KES) + Risk Score %
- **available_status_transitions**: list of valid next statuses for the "Update Status" dropdown
- **assigned_to**: Analyst name, role, avatar initial (null if unassigned)
- **timeline**: ordered list of lifecycle events with timestamps
""",
)
async def get_alert_detail(
    alert_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("view_score")),
):
    return await AlertService.get_alert_detail(db, alert_id)


# ── Update status ─────────────────────────────────────────────────────────────


@router.patch(
    "/alerts/{alert_id}/status",
    response_model=AlertDetailResponse,
    summary="Update alert status",
    description="""
Update the lifecycle state of an alert.

Valid transitions per current state:
- `OPEN`          → ACKNOWLEDGED | RESOLVED | ESCALATED
- `ACKNOWLEDGED`  → INVESTIGATING | RESOLVED | ESCALATED
- `INVESTIGATING` → RESOLVED | ESCALATED
- `ESCALATED`     → RESOLVED
- `RESOLVED`      → (terminal — no further transitions)
- `EXPIRED`       → OPEN (re-open)

Returns 422 if the requested transition is not allowed from the current state.
""",
)
async def update_alert_status(
    alert_id: uuid.UUID,
    data: AlertStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("update_case")),
):
    return await AlertService.update_status(
        db, alert_id, data, updated_by=current_user.id
    )


# ── Acknowledge ───────────────────────────────────────────────────────────────


@router.patch(
    "/alerts/{alert_id}/acknowledge",
    response_model=AlertDetailResponse,
    summary="Acknowledge an alert",
    description="Mark the alert as seen. Moves status from OPEN → ACKNOWLEDGED.",
)
async def acknowledge_alert(
    alert_id: uuid.UUID,
    data: AlertAcknowledgeRequest = AlertAcknowledgeRequest(),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("view_score")),
):
    return await AlertService.acknowledge(db, alert_id, note=data.note)


# ── Resolve ───────────────────────────────────────────────────────────────────


@router.patch(
    "/alerts/{alert_id}/resolve",
    response_model=AlertDetailResponse,
    summary="Resolve or dismiss an alert",
    description="Close the alert with a resolution note. Set `is_false_positive=true` to flag it for model feedback.",
)
async def resolve_alert(
    alert_id: uuid.UUID,
    data: AlertResolveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("update_case")),
):
    return await AlertService.resolve(db, alert_id, data, resolved_by=current_user.id)


# ── Assign ────────────────────────────────────────────────────────────────────


@router.patch(
    "/alerts/{alert_id}/assign",
    response_model=AlertDetailResponse,
    summary="Assign alert to an investigator",
    description="""
Route this alert to a specific analyst.
If the alert is still OPEN it is automatically acknowledged on assignment.
Corresponds to the 'Assign to Investigator' button in the Quick Actions sidebar.
""",
)
async def assign_alert(
    alert_id: uuid.UUID,
    data: AlertAssignRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("assign_case")),
):
    return await AlertService.assign(db, alert_id, data, assigned_by=current_user.id)
