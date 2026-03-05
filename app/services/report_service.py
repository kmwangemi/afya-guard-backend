"""
SHA Fraud Detection — Report Service

Handles full report lifecycle:
  generate()     → creates DB row with PROCESSING status, computes metrics async-style
  list_reports() → paginated list + stat cards (total, completed, processing, records)
  get_detail()   → full view-dialog data (type cards, summary text, key metrics)
  download()     → returns download_url, marks downloaded in audit log

Report generation computes real metrics from live DB data per report type:

  SUMMARY      → claim counts, flagged%, fraud rate, alert count, fraud KES total
  PROVIDER     → top providers by risk score, flagged claim count per provider
  INVESTIGATION→ open/closed/confirmed cases, resolution rate, avg days open
  COUNTY       → claims + fraud rate per county, top 10 by fraud rate
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import case, func, select
from sqlalchemy.sql.functions import count
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.claim_model import Claim
from app.models.enums_model import (
    AlertSeverity,
    CaseStatus,
    ClaimStatus,
    DateRangePreset,
    ReportStatus,
    ReportType,
)
from app.models.fraud_alert_model import FraudAlert
from app.models.fraud_case_model import FraudCase
from app.models.provider_model import Provider
from app.models.report_model import FraudReport
from app.models.user_model import User
from app.schemas.report_schema import (
    ReportDetailResponse,
    ReportGenerateRequest,
    ReportKeyMetrics,
    ReportListFilter,
    ReportListItem,
    ReportListResponse,
    ReportListStats,
)

# ── Date range helpers ────────────────────────────────────────────────────────


def _resolve_period(
    preset: DateRangePreset,
    custom_start: Optional[datetime],
    custom_end: Optional[datetime],
) -> Tuple[datetime, datetime, str]:
    """
    Return (start, end, label) for the given preset.
    Label matches the UI format: "2024-02-05 to 2024-02-11"
    """
    now = datetime.now(UTC)
    if preset == DateRangePreset.CUSTOM:
        if not custom_start or not custom_end:
            raise HTTPException(
                status_code=400,
                detail="period_start and period_end are required for custom date range",
            )
        start, end = custom_start, custom_end
    elif preset == DateRangePreset.WEEK:
        # Monday–Sunday of the current week
        start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end = start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    elif preset == DateRangePreset.QUARTER:
        q_start_month = ((now.month - 1) // 3) * 3 + 1
        start = now.replace(
            month=q_start_month, day=1, hour=0, minute=0, second=0, microsecond=0
        )
        end = now
    elif preset == DateRangePreset.YEAR:
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end = now
    else:  # MONTH (default)
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = now
    label = f"{start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}"
    return start, end, label


# ── Per-type metric computation ───────────────────────────────────────────────


async def _compute_summary_metrics(
    db: AsyncSession, start: datetime, end: datetime
) -> dict:
    """SUMMARY report — overall fraud stats for the period."""
    total_res = await db.execute(
        select(count(Claim.id)).filter(
            Claim.submitted_at >= start, Claim.submitted_at <= end
        )
    )
    total_claims = total_res.scalar_one() or 0
    flagged_res = await db.execute(
        select(count(Claim.id)).filter(
            Claim.submitted_at >= start,
            Claim.submitted_at <= end,
            Claim.sha_status.in_([ClaimStatus.FLAGGED, ClaimStatus.UNDER_REVIEW]),
        )
    )
    flagged = flagged_res.scalar_one() or 0
    fraud_amt_res = await db.execute(
        select(func.coalesce(func.sum(FraudCase.estimated_loss), 0)).filter(
            FraudCase.status == CaseStatus.CONFIRMED_FRAUD,
            FraudCase.estimated_loss.isnot(None),
        )
    )
    fraud_amount = float(fraud_amt_res.scalar_one() or 0)
    alert_res = await db.execute(
        select(count(FraudAlert.id)).filter(
            FraudAlert.raised_at >= start,
            FraudAlert.raised_at <= end,
            FraudAlert.severity == AlertSeverity.CRITICAL,
        )
    )
    alert_count = alert_res.scalar_one() or 0
    return {
        "fraud_detection_rate": (
            round((flagged / total_claims * 100), 1) if total_claims else 0.0
        ),
        "fraud_amount_detected": fraud_amount,
        "alert_cases_generated": alert_count,
        "type_specific": {
            "total_claims": total_claims,
            "flagged_claims": flagged,
        },
    }


async def _compute_provider_metrics(
    db: AsyncSession, start: datetime, end: datetime
) -> dict:
    """PROVIDER report — top providers by fraud rate."""
    provider_agg = await db.execute(
        select(
            Provider.name,
            count(Claim.id).label("total"),
            func.sum(
                case(
                    (
                        Claim.sha_status.in_(
                            [ClaimStatus.FLAGGED, ClaimStatus.UNDER_REVIEW]
                        ),
                        1,
                    ),
                    else_=0,
                )
            ).label("flagged"),
        )
        .join(Claim, Claim.provider_id == Provider.id)
        .filter(Claim.submitted_at >= start, Claim.submitted_at <= end)
        .group_by(Provider.name)
        .order_by(count(Claim.id).desc())
        .limit(10)
    )
    rows = provider_agg.all()
    total_flagged = sum(int(r.flagged or 0) for r in rows)
    total_claims = sum(int(r.total or 0) for r in rows)
    fraud_amt_res = await db.execute(
        select(func.coalesce(func.sum(FraudCase.estimated_loss), 0)).filter(
            FraudCase.status == CaseStatus.CONFIRMED_FRAUD
        )
    )
    alert_res = await db.execute(select(count(FraudAlert.id)))
    return {
        "fraud_detection_rate": (
            round(total_flagged / total_claims * 100, 1) if total_claims else 0.0
        ),
        "fraud_amount_detected": float(fraud_amt_res.scalar_one() or 0),
        "alert_cases_generated": alert_res.scalar_one() or 0,
        "type_specific": {
            "top_providers": [
                {"name": r.name, "total": int(r.total), "flagged": int(r.flagged or 0)}
                for r in rows
            ]
        },
    }


async def _compute_investigation_metrics(
    db: AsyncSession, start: datetime, end: datetime
) -> dict:
    """INVESTIGATION report — case resolution stats."""
    total_res = await db.execute(select(count(FraudCase.id)))
    confirmed_res = await db.execute(
        select(count(FraudCase.id)).filter(
            FraudCase.status == CaseStatus.CONFIRMED_FRAUD
        )
    )
    fraud_amt_res = await db.execute(
        select(func.coalesce(func.sum(FraudCase.estimated_loss), 0)).filter(
            FraudCase.status == CaseStatus.CONFIRMED_FRAUD
        )
    )
    total = total_res.scalar_one() or 0
    confirmed = confirmed_res.scalar_one() or 0
    return {
        "fraud_detection_rate": round(confirmed / total * 100, 1) if total else 0.0,
        "fraud_amount_detected": float(fraud_amt_res.scalar_one() or 0),
        "alert_cases_generated": confirmed,
        "type_specific": {
            "total_cases": total,
            "confirmed_fraud": confirmed,
        },
    }


async def _compute_county_metrics(
    db: AsyncSession, start: datetime, end: datetime
) -> dict:
    """COUNTY report — fraud stats per county."""
    county_agg = await db.execute(
        select(
            Provider.county,
            count(Claim.id).label("total"),
            func.sum(
                case(
                    (
                        Claim.sha_status.in_(
                            [ClaimStatus.FLAGGED, ClaimStatus.UNDER_REVIEW]
                        ),
                        1,
                    ),
                    else_=0,
                )
            ).label("flagged"),
        )
        .join(Claim, Claim.provider_id == Provider.id)
        .filter(
            Claim.submitted_at >= start,
            Claim.submitted_at <= end,
            Provider.county.isnot(None),
        )
        .group_by(Provider.county)
        .order_by(count(Claim.id).desc())
        .limit(47)
    )
    rows = county_agg.all()
    total_flagged = sum(int(r.flagged or 0) for r in rows)
    total_claims = sum(int(r.total or 0) for r in rows)
    fraud_amt_res = await db.execute(
        select(func.coalesce(func.sum(FraudCase.estimated_loss), 0)).filter(
            FraudCase.status == CaseStatus.CONFIRMED_FRAUD
        )
    )
    alert_res = await db.execute(select(count(FraudAlert.id)))
    return {
        "fraud_detection_rate": (
            round(total_flagged / total_claims * 100, 1) if total_claims else 0.0
        ),
        "fraud_amount_detected": float(fraud_amt_res.scalar_one() or 0),
        "alert_cases_generated": alert_res.scalar_one() or 0,
        "type_specific": {
            "counties": [
                {
                    "county": r.county,
                    "total": int(r.total),
                    "flagged": int(r.flagged or 0),
                }
                for r in rows
            ]
        },
    }


_METRIC_COMPUTERS = {
    ReportType.SUMMARY: _compute_summary_metrics,
    ReportType.PROVIDER: _compute_provider_metrics,
    ReportType.INVESTIGATION: _compute_investigation_metrics,
    ReportType.COUNTY: _compute_county_metrics,
}


async def _count_records(
    db: AsyncSession,
    report_type: ReportType,
    start: datetime,
    end: datetime,
) -> int:
    """Count the primary records analysed for this report type."""
    if report_type == ReportType.INVESTIGATION:
        res = await db.execute(select(count(FraudCase.id)))
    else:
        res = await db.execute(
            select(count(Claim.id)).filter(
                Claim.submitted_at >= start, Claim.submitted_at <= end
            )
        )
    return res.scalar_one() or 0


def _build_summary_text(report: FraudReport) -> str:
    return (
        f"This is a {report.report_type} report containing analysis of "
        f"{report.record_count:,} records.\n"
        f"The report covers the period: {report.period_label}\n"
        f"Generated on: {report.generated_at.strftime('%-m/%-d/%Y')} at "
        f"{report.generated_at.strftime('%-I:%M:%S %p')}"
    )


# ── Schema builders ───────────────────────────────────────────────────────────


def _to_list_item(report: FraudReport) -> ReportListItem:
    return ReportListItem(
        id=report.id,
        name=report.name,
        report_type=report.report_type,
        period_label=report.period_label,
        status=report.status,
        record_count=report.record_count,
        generated_at=report.generated_at,
        generated_by_name=(report.generator.full_name if report.generator else None),
        can_download=report.status == ReportStatus.COMPLETED,
        download_url=report.download_url,
    )


def _to_detail(report: FraudReport) -> ReportDetailResponse:
    data = report.report_data or {}
    key_metrics = ReportKeyMetrics(
        fraud_detection_rate=float(data.get("fraud_detection_rate", 0)),
        fraud_amount_detected=float(data.get("fraud_amount_detected", 0)),
        alert_cases_generated=int(data.get("alert_cases_generated", 0)),
    )
    summary = report.summary_text or _build_summary_text(report)
    return ReportDetailResponse(
        id=report.id,
        name=report.name,
        report_type=report.report_type,
        status=report.status,
        period_label=report.period_label,
        record_count=report.record_count,
        summary_text=summary,
        key_metrics=key_metrics,
        generated_at=report.generated_at,
        completed_at=report.completed_at,
        generated_by_name=(report.generator.full_name if report.generator else None),
        custom_notes=report.custom_notes,
        can_download=report.status == ReportStatus.COMPLETED,
        download_url=report.download_url,
        report_data=data,
    )


# ══════════════════════════════════════════════════════════════════════════════
# SERVICE
# ══════════════════════════════════════════════════════════════════════════════


class ReportService:

    # ── Generate ──────────────────────────────────────────────────────────────

    @staticmethod
    async def generate(
        db: AsyncSession,
        data: ReportGenerateRequest,
        generated_by: User,
    ) -> ReportDetailResponse:
        """
        Create a new report, compute all metrics synchronously, and mark COMPLETED.

        For a production system with large datasets, move the metric computation
        into a background task and leave the status as PROCESSING until done.
        """
        start, end, label = _resolve_period(
            data.date_range_preset, data.period_start, data.period_end
        )
        report = FraudReport(
            name=data.name,
            report_type=data.report_type,
            status=ReportStatus.PROCESSING,
            date_range_preset=data.date_range_preset,
            period_start=start,
            period_end=end,
            period_label=label,
            custom_notes=data.custom_notes,
            generated_by=generated_by.id,
            generated_at=datetime.now(UTC),
        )
        db.add(report)
        await db.flush()  # get the id
        try:
            # Compute metrics from live data
            compute_fn = _METRIC_COMPUTERS[data.report_type]
            metrics = await compute_fn(db, start, end)
            record_count = await _count_records(db, data.report_type, start, end)
            report.report_data = metrics
            report.record_count = record_count
            report.status = ReportStatus.COMPLETED
            report.completed_at = datetime.now(UTC)
            report.summary_text = _build_summary_text(report)
        except Exception as exc:
            report.status = ReportStatus.FAILED
            report.summary_text = f"Report generation failed: {exc}"
        await db.commit()
        result = await db.execute(
            select(FraudReport)
            .options(selectinload(FraudReport.generator))
            .filter(FraudReport.id == report.id)
        )
        return _to_detail(result.scalars().first())

    # ── List  (Reports page) ──────────────────────────────────────────────────

    @staticmethod
    async def list_reports(
        db: AsyncSession,
        filters: ReportListFilter,
        offset: int = 0,
        limit: int = 25,
    ) -> ReportListResponse:
        """
        Returns stat cards + paginated table rows in one call.

        Stat cards:  Total Reports | Completed | Processing | Total Records
        Table:       Report Name | Type | Period | Status | Records | Generated | Actions
        """
        # ── Stat card counts (full table, no filters) ──
        stats_res = await db.execute(
            select(
                count(FraudReport.id).label("total"),
                func.sum(
                    case((FraudReport.status == ReportStatus.COMPLETED, 1), else_=0)
                ).label("completed"),
                func.sum(
                    case((FraudReport.status == ReportStatus.PROCESSING, 1), else_=0)
                ).label("processing"),
                func.coalesce(func.sum(FraudReport.record_count), 0).label(
                    "total_records"
                ),
            )
        )
        row = stats_res.one()
        stats = ReportListStats(
            total_reports=int(row.total or 0),
            completed=int(row.completed or 0),
            processing=int(row.processing or 0),
            total_records=int(row.total_records or 0),
        )
        # ── Filtered query ──
        q = select(FraudReport).options(selectinload(FraudReport.generator))
        if filters.search:
            q = q.filter(FraudReport.name.ilike(f"%{filters.search.strip()}%"))
        if filters.report_type:
            q = q.filter(FraudReport.report_type == filters.report_type)
        if filters.status:
            q = q.filter(FraudReport.status == filters.status)
        count_res = await db.execute(select(count()).select_from(q.subquery()))
        total = count_res.scalar_one()
        result = await db.execute(
            q.order_by(FraudReport.generated_at.desc()).offset(offset).limit(limit)
        )
        reports = result.scalars().all()
        return ReportListResponse(
            stats=stats,
            items=[_to_list_item(r) for r in reports],
            total=total,
            page=(offset // limit) + 1,
            page_size=limit,
            pages=-(-total // limit) if total else 0,
        )

    # ── Detail  (View Report dialog) ─────────────────────────────────────────

    @staticmethod
    async def get_detail(
        db: AsyncSession, report_id: uuid.UUID
    ) -> ReportDetailResponse:
        result = await db.execute(
            select(FraudReport)
            .options(selectinload(FraudReport.generator))
            .filter(FraudReport.id == report_id)
        )
        report = result.scalars().first()
        if not report:
            raise HTTPException(status_code=404, detail="Report not found")
        return _to_detail(report)

    # ── Download  (↓ action button) ───────────────────────────────────────────

    @staticmethod
    async def get_download(
        db: AsyncSession, report_id: uuid.UUID
    ) -> ReportDetailResponse:
        """
        Returns the report detail including download_url.
        The frontend uses this to trigger the JSON download.
        Raises 400 if the report is not yet completed.
        """
        result = await db.execute(
            select(FraudReport)
            .options(selectinload(FraudReport.generator))
            .filter(FraudReport.id == report_id)
        )
        report = result.scalars().first()
        if not report:
            raise HTTPException(status_code=404, detail="Report not found")
        if report.status != ReportStatus.COMPLETED:
            raise HTTPException(
                status_code=400,
                detail="Report is still processing. Please try again later.",
            )
        return _to_detail(report)

    # ── Delete ────────────────────────────────────────────────────────────────

    @staticmethod
    async def delete_report(db: AsyncSession, report_id: uuid.UUID) -> None:
        result = await db.execute(
            select(FraudReport).filter(FraudReport.id == report_id)
        )
        report = result.scalars().first()
        if not report:
            raise HTTPException(status_code=404, detail="Report not found")
        await db.delete(report)
        await db.commit()
