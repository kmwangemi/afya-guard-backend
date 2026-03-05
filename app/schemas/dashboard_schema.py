"""
SHA Fraud Detection — Dashboard Analytics Schemas

Shaped to match both the frontend TypeScript interfaces and Dashboard-stats.png:

  STAT CARDS  (top row)
    Total Claims Processed  |  Flagged Claims  |
    Critical Alerts         |  Fraud Prevented (Ksh)

    Each card includes: current value + % change from last month (↑ / ↓)

  30-DAY TREND CHART
    Per-day: date, total_claims, flagged_claims, fraud_rate
    Matches TrendData interface exactly.

  RISK DISTRIBUTION  (right panel)
    Critical  847  (2.8%)
    High     3,245 (12.3%)
    Medium   6,547 (24.8%)
    Low     15,928 (60.1%)
    Total   26,567

  TOP 10 COUNTIES BY FRAUD RATE  (table)
    County | Total Claims | Flagged | Fraud Rate | Est. Amount
    Matches CountyFraudData interface exactly.

  RECENT CRITICAL ALERTS  (bottom)
    Thin wrapper — reuses AlertListItem from alert_schema.
"""

from typing import List

from app.schemas.base_schema import BaseSchema


# ══════════════════════════════════════════════════════════════════════════════
# STAT CARDS  (top row of dashboard)
# ══════════════════════════════════════════════════════════════════════════════


class StatCard(BaseSchema):
    """
    One of the four top-row stat cards (Dashboard-stats.png).

    value           — current period value
    change_percent  — % change vs same period last month (positive = up)
    direction       — "up" | "down"  (drives the ↑ / ↓ arrow colour)
    """

    value: float
    change_percent: float = 0.0  # e.g. 12.0 → "12% from last month"
    direction: str = "up"  # "up" (green arrow) | "down" (red arrow)


class DashboardStats(BaseSchema):
    """
    Four stat cards that match the TypeScript DashboardStats interface.

    Frontend field names preserved exactly so the API response can be
    consumed without transformation.
    """

    totalClaimsProcessed: int  # e.g. 124567
    flaggedClaims: int  # e.g. 3245
    criticalAlerts: int  # e.g. 89
    estimatedFraudPrevented: float  # e.g. 245680000  (raw KES)

    # Per-card change metadata (not in the TS interface but shown in the UI)
    totalClaimsChange: float = 0.0  # "↑ 12% from last month"
    flaggedClaimsChange: float = 0.0  # "↑ 8% from last month"
    criticalAlertsChange: float = 0.0  # "↓ 3% from last month"
    fraudPreventedChange: float = 0.0  # "↑ 25% from last month"


# ══════════════════════════════════════════════════════════════════════════════
# 30-DAY TREND  (matches TrendData TypeScript interface exactly)
# ══════════════════════════════════════════════════════════════════════════════


class TrendData(BaseSchema):
    """
    One day's data point for the 30-Day Trend chart.
    Field names match the TypeScript TrendData interface.
    """

    date: str  # "2026-02-04"  (ISO date string)
    totalClaims: int
    flaggedClaims: int
    fraudRate: float  # 0.0 – 1.0  (e.g. 0.05 = 5%)


# ══════════════════════════════════════════════════════════════════════════════
# RISK DISTRIBUTION  (right panel, Dashboard-stats.png)
# ══════════════════════════════════════════════════════════════════════════════


class RiskDistributionItem(BaseSchema):
    """
    One row in the Risk Distribution panel.

    Critical  847  (2.8%)   — purple bar
    High     3,245 (12.3%)  — red bar
    Medium   6,547 (24.8%)  — orange/yellow bar
    Low     15,928 (60.1%)  — green bar
    """

    label: str  # "Critical" | "High" | "Medium" | "Low"
    risk_level: str  # "CRITICAL" | "HIGH" | "MEDIUM" | "LOW"
    count: int
    percentage: float  # e.g. 2.8
    colour: str  # "purple" | "red" | "orange" | "green"


class RiskDistribution(BaseSchema):
    items: List[RiskDistributionItem] = []
    total_claims: int = 0  # "Total claims  26,567"


# ══════════════════════════════════════════════════════════════════════════════
# COUNTY FRAUD TABLE  (matches CountyFraudData TypeScript interface exactly)
# ══════════════════════════════════════════════════════════════════════════════


class CountyFraudData(BaseSchema):
    """
    One row in the 'Top 10 Counties by Fraud Rate' table.
    Field names match the TypeScript CountyFraudData interface.
    """

    county: str  # "Nairobi"
    totalClaims: int
    flaggedClaims: int
    fraudRate: float  # 0.0 – 1.0
    estimatedAmount: float  # raw KES


# ══════════════════════════════════════════════════════════════════════════════
# FULL DASHBOARD RESPONSE  (single call returns everything)
# ══════════════════════════════════════════════════════════════════════════════


class DashboardResponse(BaseSchema):
    """
    Single endpoint that returns all data needed to render the dashboard.
    Avoids the frontend making 4 separate calls on page load.
    """

    stats: DashboardStats
    trend: List[TrendData] = []
    risk_distribution: RiskDistribution = RiskDistribution()
    top_counties: List[CountyFraudData] = []
    # recent_critical_alerts lives in AlertListItem — imported in routes
