"""
SHA Fraud Detection — Report Routes

GET    /api/v1/reports                  List page — stat cards + paginated table
POST   /api/v1/reports                  Generate New Report (dialog)
GET    /api/v1/reports/{id}             View Report dialog data
GET    /api/v1/reports/{id}/download    Download action (↓ button)
DELETE /api/v1/reports/{id}             Delete report
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import PaginationParams, get_db, require_permission
from app.models.enums_model import ReportStatus, ReportType
from app.models.user_model import User
from app.schemas.report_schema import (
    ReportDetailResponse,
    ReportGenerateRequest,
    ReportListFilter,
    ReportListResponse,
)
from app.services.report_service import ReportService

router = APIRouter(tags=["Reports"])


# ── List reports (Reports page load) ─────────────────────────────────────────


@router.get(
    "/reports",
    response_model=ReportListResponse,
    summary="List reports",
    description="""
Returns stat cards + paginated report table in one call (Reports page).

**Stat cards** (top row):
- `stats.total_reports`  — Total Reports
- `stats.completed`      — Completed (green)
- `stats.processing`     — Processing (purple)
- `stats.total_records`  — Total Records (sum of record_count)

**Table columns:** Report Name | Type | Period | Status badge | Records | Generated | Actions

**Status badge colours:**
- `completed`  → green
- `processing` → blue/purple
- `scheduled`  → yellow
- `failed`     → red

**Filters:**
- `search`       — partial match on report name
- `report_type`  — `summary` | `provider` | `investigation` | `county`
- `status`       — `completed` | `processing` | `scheduled` | `failed`

**Actions per row:**
- `can_download = true` → show ↓ download button (only when completed)
- Eye (👁) → always enabled → calls GET /reports/{id}
""",
)
async def list_reports(
    search: Optional[str] = Query(None, description="Search by report name"),
    report_type: Optional[str] = Query(None, description="Filter by report type"),
    status: Optional[str] = Query(None, description="Filter by status"),
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("view_analytics")),
):
    try:
        type_filter = ReportType(report_type) if report_type else None
    except ValueError:
        raise HTTPException(
            422,
            f"Invalid report_type '{report_type}'. Valid: {[t.value for t in ReportType]}",
        )

    try:
        status_filter = ReportStatus(status) if status else None
    except ValueError:
        raise HTTPException(
            422, f"Invalid status '{status}'. Valid: {[s.value for s in ReportStatus]}"
        )

    filters = ReportListFilter(
        search=search,
        report_type=type_filter,
        status=status_filter,
    )

    return await ReportService.list_reports(
        db, filters, offset=pagination.offset, limit=pagination.page_size
    )


# ── Generate report (Generate New Report dialog) ──────────────────────────────


@router.post(
    "/reports",
    response_model=ReportDetailResponse,
    status_code=201,
    summary="Generate new report",
    description="""
Trigger generation of a new fraud detection report.
Maps to the 'Generate New Report' dialog (Report_2.png / Report_3.png).

**Required fields:**
- `name`        — Report Name (e.g. "Monthly Fraud Analysis")
- `report_type` — `summary` | `provider` | `investigation` | `county`

**Optional:**
- `date_range_preset` — `week` | `month` (default) | `quarter` | `year` | `custom`
- `period_start` / `period_end` — required when preset = `custom`
- `custom_notes` — Additional Notes textarea

**Note:** Generation computes real metrics from live DB data.
The response is returned with `status = completed` once metrics are computed.
For very large datasets, move computation to a background task.
""",
)
async def generate_report(
    data: ReportGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("view_analytics")),
):
    return await ReportService.generate(db, data, generated_by=current_user)


# ── View report detail (👁 button / View Report dialog) ───────────────────────


@router.get(
    "/reports/{report_id}",
    response_model=ReportDetailResponse,
    summary="Get report detail",
    description="""
Returns all data for the View Report dialog.

**4 info cards:** Report Type | Status | Period | Records Analyzed

**Report Summary card:**
`summary_text` — e.g. "This is a summary report containing analysis of 1,250 records..."

**Key Metrics card:**
- `key_metrics.fraud_detection_rate`   → blue   "12.0%"
- `key_metrics.fraud_amount_detected`  → green  KES amount
- `key_metrics.alert_cases_generated`  → orange integer

**Footer buttons:**
- Close (always enabled)
- Download Report — enabled only when `can_download = true`
""",
)
async def get_report(
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("view_analytics")),
):
    return await ReportService.get_detail(db, report_id)


# ── Download (↓ action button) ────────────────────────────────────────────────


@router.get(
    "/reports/{report_id}/download",
    response_model=ReportDetailResponse,
    summary="Download report",
    description="""
Returns the report detail with `download_url` populated.
Returns 400 if the report is not yet completed.

The frontend uses this to:
1. Get the report data
2. Construct the JSON download (matching the frontend `handleDownloadReport` pattern)
3. Or redirect to `download_url` if a file was stored externally
""",
)
async def download_report(
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("view_analytics")),
):
    return await ReportService.get_download(db, report_id)


# ── Delete report ─────────────────────────────────────────────────────────────


@router.delete(
    "/reports/{report_id}",
    status_code=204,
    summary="Delete report",
)
async def delete_report(
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("view_analytics")),
):
    await ReportService.delete_report(db, report_id)
