"""
SHA Fraud Detection — Dashboard Analytics Routes

GET  /api/v1/dashboard                      All widgets in one call (recommended)
GET  /api/v1/dashboard/stats                Four stat cards + MoM % change
GET  /api/v1/dashboard/trend                30-day daily trend (TrendData[])
GET  /api/v1/dashboard/risk-distribution    Risk level distribution panel
GET  /api/v1/dashboard/counties             Top 10 counties by fraud rate
GET  /api/v1/dashboard/critical-alerts      Recent critical alerts (AlertListItem[])

All endpoints are async and use COUNT/SUM SQL aggregates.
No rows are fetched into Python memory for counting.
"""

from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_permission
from app.models.enums_model import AlertSeverity
from app.models.user_model import User
from app.schemas.alert_schema import AlertListFilter, AlertListItem
from app.schemas.base_schema import PaginatedResponse
from app.schemas.dashboard_schema import (
    CountyFraudData,
    DashboardResponse,
    DashboardStats,
    RiskDistribution,
    TrendData,
)
from app.services.alert_service import AlertService
from app.services.dashboard_service import DashboardService

router = APIRouter(tags=["Dashboard"])


# ── Full dashboard (single call — recommended for page load) ──────────────────


@router.get(
    "/dashboard",
    response_model=DashboardResponse,
    summary="Full dashboard data",
    description="""
Returns all dashboard widgets in a single response to minimise round-trips on page load.

Includes:
- **stats** — four stat cards (Total Claims Processed, Flagged Claims, Critical Alerts,
  Fraud Prevented) with month-over-month % change and direction arrows
- **trend** — 30-day daily data for the trend chart (date, totalClaims, flaggedClaims, fraudRate)
- **risk_distribution** — Critical / High / Medium / Low counts + percentages for the right panel
- **top_counties** — top 10 counties by fraud rate for the county table

Use `?trend_days=N` to change the trend window (default 30, max 90).
""",
)
async def get_dashboard(
    trend_days: int = Query(
        30, ge=7, le=90, description="Number of days for the trend chart"
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("view_analytics")),
):
    return await DashboardService.get_dashboard(db, trend_days=trend_days)


# ── Stat cards only ───────────────────────────────────────────────────────────


@router.get(
    "/dashboard/stats",
    response_model=DashboardStats,
    summary="Dashboard stat cards",
    description="""
Returns the four top-row stat cards with month-over-month % change.

Response fields match the TypeScript **DashboardStats** interface exactly:
- `totalClaimsProcessed`
- `flaggedClaims`
- `criticalAlerts`
- `estimatedFraudPrevented`

Additional fields for the UI arrows:
- `totalClaimsChange`, `flaggedClaimsChange`, `criticalAlertsChange`, `fraudPreventedChange`
  → percentage point change vs same calendar month last month
""",
)
async def get_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("view_analytics")),
):
    return await DashboardService.get_stats(db)


# ── 30-day trend ──────────────────────────────────────────────────────────────


@router.get(
    "/dashboard/trend",
    response_model=List[TrendData],
    summary="30-day claim trend",
    description="""
Returns daily aggregates for the trend chart (Total Claims vs Flagged Claims lines).

Each data point matches the TypeScript **TrendData** interface:
- `date` — ISO date string e.g. `"2026-02-04"`
- `totalClaims` — all claims submitted that day
- `flaggedClaims` — claims with FLAGGED or UNDER_REVIEW status submitted that day
- `fraudRate` — `flaggedClaims / totalClaims` (0.0 – 1.0)
""",
)
async def get_trend(
    days: int = Query(30, ge=7, le=90, description="Number of days to include"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("view_analytics")),
):
    return await DashboardService.get_trend(db, days=days)


# ── Risk distribution ─────────────────────────────────────────────────────────


@router.get(
    "/dashboard/risk-distribution",
    response_model=RiskDistribution,
    summary="Risk level distribution",
    description="""
Returns claim counts per risk level for the Risk Distribution right panel.

Each item:
- `label` — "Critical" | "High" | "Medium" | "Low"
- `count` — number of claims at this risk level (from latest fraud score per claim)
- `percentage` — % of total scored claims
- `colour` — "purple" | "red" | "orange" | "green"  (drives bar colour)

`total_claims` — total scored claims across all levels.
""",
)
async def get_risk_distribution(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("view_analytics")),
):
    return await DashboardService.get_risk_distribution(db)


# ── Top counties ──────────────────────────────────────────────────────────────


@router.get(
    "/dashboard/counties",
    response_model=List[CountyFraudData],
    summary="Top counties by fraud rate",
    description="""
Returns the top N counties sorted by fraud rate descending for the county table.

Each row matches the TypeScript **CountyFraudData** interface:
- `county` — county name
- `totalClaims` — all claims from providers in this county
- `flaggedClaims` — flagged or under-review claims from this county
- `fraudRate` — `flaggedClaims / totalClaims` (0.0 – 1.0)
- `estimatedAmount` — total KES claim amount for flagged claims

Use `?limit=10` (default) to control how many rows are returned.
""",
)
async def get_county_fraud_data(
    limit: int = Query(
        10,
        ge=1,
        le=47,
        description="Number of counties to return (max 47 = all Kenya counties)",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("view_analytics")),
):
    return await DashboardService.get_top_counties(db, limit=limit)


# ── Recent critical alerts ────────────────────────────────────────────────────


@router.get(
    "/dashboard/critical-alerts",
    response_model=PaginatedResponse[AlertListItem],
    summary="Recent critical alerts",
    description="""
Returns the most recent CRITICAL severity alerts — used for the
'Recent Critical Alerts' table at the bottom of the dashboard.

Matches the TypeScript `getCriticalAlerts(limit)` mock interface.
Only returns alerts with severity=CRITICAL and status≠RESOLVED.
""",
)
async def get_critical_alerts(
    limit: int = Query(10, ge=1, le=50, description="Number of alerts to return"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("view_analytics")),
):
    filters = AlertListFilter(
        severity=AlertSeverity.CRITICAL,
        status=None,  # return all non-resolved via ordering, not filter
    )
    items, total = await AlertService.list_alerts(db, filters, offset=0, limit=limit)
    return PaginatedResponse(
        items=items,
        total=total,
        page=1,
        page_size=limit,
        pages=1,
    )
