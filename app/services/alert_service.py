"""
SHA Fraud Detection — Alert Service

Handles: alert listing with all UI filters, full alert detail assembly,
         status updates, assignment, acknowledgement, and resolution.

All display strings (alert_number, type_display, subtitle) are derived here
so the frontend receives ready-to-render values.
"""

import uuid
from datetime import UTC, datetime
from typing import List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.functions import count

from app.models.claim_model import Claim
from app.models.enums_model import AlertStatus, AlertType
from app.models.fraud_alert_model import FraudAlert
from app.models.provider_model import Provider
from app.models.user_model import User
from app.schemas.alert_schema import (
    AlertAssignRequest,
    AlertDetailResponse,
    AlertFraudAnalysis,
    AlertListFilter,
    AlertListItem,
    AlertResolveRequest,
    AlertStatusUpdate,
    AlertSummary,
    AssignedAnalyst,
    RelatedClaim,
    TimelineEvent,
)

# ── Display helpers ────────────────────────────────────────────────────────────

# Maps AlertType enum → human-readable UI label (matches Alert-main-page.png)
_TYPE_LABELS: dict[AlertType, str] = {
    AlertType.HIGH_RISK_SCORE: "High Risk Claim",
    AlertType.CRITICAL_RISK_SCORE: "High Risk Claim",
    AlertType.DUPLICATE_CLAIM: "Duplicate Claim",
    AlertType.PHANTOM_PATIENT: "Phantom Patient",
    AlertType.UPCODING_DETECTED: "Upcoding Detected",
    AlertType.PROVIDER_ANOMALY: "Provider Anomaly",
    AlertType.RULE_THRESHOLD_BREACH: "Pattern Detected",
    AlertType.MEMBER_FREQUENCY_ABUSE: "Pattern Detected",
    AlertType.PROVIDER_CLAIM_SPIKE: "Pattern Detected",
    AlertType.LATE_NIGHT_SUBMISSION: "Pattern Detected",
    AlertType.BULK_SUBMISSION: "Pattern Detected",
    AlertType.MODEL_CONFIDENCE_LOW: "Pattern Detected",
    AlertType.RESUBMISSION_PATTERN: "Pattern Detected",
}


def _type_display(alert_type: AlertType) -> str:
    return _TYPE_LABELS.get(alert_type, alert_type.value.replace("_", " ").title())


def _alert_number(alert: FraudAlert) -> str:
    """
    Format the alert's sequential display number.
    Uses the first 8 hex chars of the UUID as a stable numeric-looking suffix,
    zero-padded to 5 digits. Replace with a real sequence column if needed.
    """
    # Try to get a stored number from metadata first
    if alert.metadata and alert.metadata.get("alert_number"):
        return str(alert.metadata["alert_number"])
    # Derive a stable display number from UUID
    short = int(str(alert.id).replace("-", "")[:8], 16) % 100000
    return f"ALERT-{short:05d}"


def _subtitle(alert: FraudAlert) -> str:
    """Short descriptive subtitle shown under the alert number in the detail header."""
    subtitles = {
        AlertType.DUPLICATE_CLAIM: "Alert: duplicate claim",
        AlertType.PHANTOM_PATIENT: "Alert: phantom patient detected",
        AlertType.UPCODING_DETECTED: "Alert: upcoding detected",
        AlertType.PROVIDER_ANOMALY: "Alert: provider anomaly",
        AlertType.HIGH_RISK_SCORE: "Alert: high risk claim",
        AlertType.CRITICAL_RISK_SCORE: "Alert: critical risk score",
        AlertType.RULE_THRESHOLD_BREACH: "Alert: fraud pattern detected",
        AlertType.BULK_SUBMISSION: "Alert: bulk claim submission",
        AlertType.PROVIDER_CLAIM_SPIKE: "Alert: provider claim spike",
    }
    return subtitles.get(
        alert.alert_type, f"Alert: {_type_display(alert.alert_type).lower()}"
    )


# Valid status transitions from each state
_STATUS_TRANSITIONS: dict[AlertStatus, list[AlertStatus]] = {
    AlertStatus.OPEN: [
        AlertStatus.ACKNOWLEDGED,
        AlertStatus.RESOLVED,
        AlertStatus.ESCALATED,
    ],
    AlertStatus.ACKNOWLEDGED: [
        AlertStatus.INVESTIGATING,
        AlertStatus.RESOLVED,
        AlertStatus.ESCALATED,
    ],
    AlertStatus.INVESTIGATING: [AlertStatus.RESOLVED, AlertStatus.ESCALATED],
    AlertStatus.ESCALATED: [AlertStatus.RESOLVED],
    AlertStatus.RESOLVED: [],
    AlertStatus.EXPIRED: [AlertStatus.OPEN],
}


def _load_alert():
    """Eager-load all relationships needed to build alert responses."""
    return select(FraudAlert).options(
        selectinload(FraudAlert.claim).selectinload(Claim.provider),
        selectinload(FraudAlert.fraud_score),
        selectinload(FraudAlert.assigned_analyst),
        selectinload(FraudAlert.resolver),
    )


def _build_list_item(alert: FraudAlert) -> AlertListItem:
    """Build one row for the alerts list table."""
    provider = alert.claim.provider if alert.claim else None
    fraud_amount = (
        float(alert.claim.total_claim_amount)
        if alert.claim and alert.claim.total_claim_amount
        else None
    )
    return AlertListItem(
        id=alert.id,
        alert_number=_alert_number(alert),
        type_display=_type_display(alert.alert_type),
        alert_type=alert.alert_type,
        provider_name=provider.name if provider else None,
        provider_id=provider.id if provider else None,
        status=alert.status,
        severity=alert.severity,
        fraud_amount=fraud_amount,
        created_at=alert.raised_at,
        claim_id=alert.claim_id,
        sha_claim_id=alert.claim.sha_claim_id if alert.claim else None,
    )


def _build_detail(alert: FraudAlert) -> AlertDetailResponse:
    """Assemble the full AlertDetailResponse matching alert-details-page.png."""
    claim = alert.claim
    provider = claim.provider if claim else None

    # ── Risk score ────────────────────────────────────────────────────────────
    raw_score = float(alert.score_at_alert) if alert.score_at_alert else None
    # score_at_alert stored as 0–1 probability or 0–100 — normalise to 0–100
    if raw_score is not None and raw_score <= 1.0:
        risk_pct = round(raw_score * 100, 1)
    else:
        risk_pct = round(raw_score, 1) if raw_score is not None else None

    # ── Alert Summary ─────────────────────────────────────────────────────────
    summary = AlertSummary(
        alert_type=alert.alert_type,
        type_display=_type_display(alert.alert_type),
        severity=alert.severity,
        status=alert.status,
        created_at=alert.raised_at,
    )

    # ── Related Claim ─────────────────────────────────────────────────────────
    related = None
    if claim:
        related = RelatedClaim(
            claim_id=claim.id,
            sha_claim_id=claim.sha_claim_id,
            provider_id=provider.id if provider else None,
            provider_name=provider.name if provider else None,
        )

    # ── Fraud Analysis ────────────────────────────────────────────────────────
    fraud_analysis = AlertFraudAnalysis(
        estimated_fraud_amount=(
            float(claim.total_claim_amount)
            if claim and claim.total_claim_amount
            else None
        ),
        risk_score_percentage=risk_pct,
    )

    # ── Assigned To ───────────────────────────────────────────────────────────
    assigned = None
    if alert.assigned_analyst:
        analyst = alert.assigned_analyst
        # Derive role from first role name if available, else "Investigator"
        role = "Investigator"
        if hasattr(analyst, "roles") and analyst.roles:
            role = analyst.roles[0].display_name
        assigned = AssignedAnalyst(
            user_id=analyst.id,
            full_name=analyst.full_name,
            role=role,
            avatar_initial=(analyst.full_name[0].upper() if analyst.full_name else "?"),
        )

    # ── Timeline ──────────────────────────────────────────────────────────────
    timeline: list[TimelineEvent] = [
        TimelineEvent(label="Alert Created", timestamp=alert.raised_at)
    ]
    if alert.acknowledged_at:
        timeline.append(
            TimelineEvent(label="Acknowledged", timestamp=alert.acknowledged_at)
        )
    if alert.escalated_at:
        timeline.append(
            TimelineEvent(label="Escalated to Case", timestamp=alert.escalated_at)
        )
    if alert.resolved_at:
        timeline.append(
            TimelineEvent(
                label="Resolved",
                timestamp=alert.resolved_at,
                note=alert.resolution_note,
            )
        )

    return AlertDetailResponse(
        id=alert.id,
        alert_number=_alert_number(alert),
        subtitle=_subtitle(alert),
        alert_summary=summary,
        related_claim=related,
        description=alert.message,
        fraud_analysis=fraud_analysis,
        available_status_transitions=_STATUS_TRANSITIONS.get(alert.status, []),
        assigned_to=assigned,
        timeline=timeline,
        alert_type=alert.alert_type,
        severity=alert.severity,
        status=alert.status,
        fraud_case_id=alert.fraud_case_id,
        metadata=alert.metadata,
    )


# ══════════════════════════════════════════════════════════════════════════════
# SERVICE
# ══════════════════════════════════════════════════════════════════════════════


class AlertService:

    # ── List  (Alert-main-page.png) ───────────────────────────────────────────

    @staticmethod
    async def list_alerts(
        db: AsyncSession,
        filters: AlertListFilter,
        offset: int = 0,
        limit: int = 25,
    ) -> Tuple[List[AlertListItem], int]:
        """
        Returns (items, total).
        Each AlertListItem matches one row in Alert-main-page.png:
          Alert Number | Type | Provider | Status | Severity | Fraud Amount | Created

        Supports:
          search   — ILIKE on alert number metadata OR provider name
          severity — exact match on AlertSeverity
          status   — exact match on AlertStatus
        """
        q = (
            _load_alert()
            .join(Claim, FraudAlert.claim_id == Claim.id, isouter=True)
            .join(Provider, Claim.provider_id == Provider.id, isouter=True)
        )

        # Search — provider name (alert_number is derived, not stored as a column)
        if filters.search:
            term = f"%{filters.search.strip()}%"
            q = q.filter(
                or_(
                    Provider.name.ilike(term),
                    FraudAlert.title.ilike(term),
                )
            )

        if filters.severity:
            q = q.filter(FraudAlert.severity == filters.severity)

        if filters.status:
            q = q.filter(FraudAlert.status == filters.status)

        if filters.alert_type:
            q = q.filter(FraudAlert.alert_type == filters.alert_type)

        if filters.provider_id:
            q = q.filter(Claim.provider_id == filters.provider_id)

        if filters.assigned_to:
            q = q.filter(FraudAlert.assigned_to == filters.assigned_to)

        if filters.raised_from:
            q = q.filter(FraudAlert.raised_at >= filters.raised_from)

        if filters.raised_to:
            q = q.filter(FraudAlert.raised_at <= filters.raised_to)

        # Total count
        count_result = await db.execute(select(count()).select_from(q.subquery()))
        total = count_result.scalar_one()

        # Paginated rows — newest first
        result = await db.execute(
            q.order_by(FraudAlert.raised_at.desc()).offset(offset).limit(limit)
        )
        alerts = result.scalars().all()

        return [_build_list_item(a) for a in alerts], total

    # ── Get single alert ──────────────────────────────────────────────────────

    @staticmethod
    async def get_alert(db: AsyncSession, alert_id: uuid.UUID) -> FraudAlert:
        result = await db.execute(_load_alert().filter(FraudAlert.id == alert_id))
        alert = result.scalars().first()
        if not alert:
            raise HTTPException(status_code=404, detail="Alert not found")
        return alert

    @staticmethod
    async def get_alert_detail(
        db: AsyncSession, alert_id: uuid.UUID
    ) -> AlertDetailResponse:
        alert = await AlertService.get_alert(db, alert_id)
        return _build_detail(alert)

    # ── Update status ─────────────────────────────────────────────────────────

    @staticmethod
    async def update_status(
        db: AsyncSession,
        alert_id: uuid.UUID,
        data: AlertStatusUpdate,
        updated_by: Optional[uuid.UUID] = None,
    ) -> AlertDetailResponse:
        alert = await AlertService.get_alert(db, alert_id)

        allowed = _STATUS_TRANSITIONS.get(alert.status, [])
        if data.status not in allowed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Cannot move from {alert.status.value} → {data.status.value}. "
                    f"Allowed transitions: {[s.value for s in allowed]}"
                ),
            )

        now = datetime.now(UTC)
        alert.status = data.status

        if data.status == AlertStatus.ACKNOWLEDGED:
            alert.acknowledged_at = now
        elif data.status == AlertStatus.RESOLVED:
            alert.resolved_at = now
            alert.resolved_by = updated_by
            if data.note:
                alert.resolution_note = data.note
            if data.is_false_positive is not None:
                alert.is_false_positive = data.is_false_positive
        elif data.status == AlertStatus.ESCALATED:
            alert.escalated_at = now

        await db.commit()

        result = await db.execute(_load_alert().filter(FraudAlert.id == alert_id))
        alert = result.scalars().first()
        return _build_detail(alert)

    # ── Acknowledge ───────────────────────────────────────────────────────────

    @staticmethod
    async def acknowledge(
        db: AsyncSession,
        alert_id: uuid.UUID,
        note: Optional[str] = None,
    ) -> AlertDetailResponse:
        return await AlertService.update_status(
            db,
            alert_id,
            AlertStatusUpdate(status=AlertStatus.ACKNOWLEDGED, note=note),
        )

    # ── Resolve ───────────────────────────────────────────────────────────────

    @staticmethod
    async def resolve(
        db: AsyncSession,
        alert_id: uuid.UUID,
        data: AlertResolveRequest,
        resolved_by: Optional[uuid.UUID] = None,
    ) -> AlertDetailResponse:
        return await AlertService.update_status(
            db,
            alert_id,
            AlertStatusUpdate(
                status=AlertStatus.RESOLVED,
                note=data.resolution_note,
                is_false_positive=data.is_false_positive,
            ),
            updated_by=resolved_by,
        )

    # ── Assign ────────────────────────────────────────────────────────────────

    @staticmethod
    async def assign(
        db: AsyncSession,
        alert_id: uuid.UUID,
        data: AlertAssignRequest,
        assigned_by: Optional[uuid.UUID] = None,
    ) -> AlertDetailResponse:
        alert = await AlertService.get_alert(db, alert_id)

        # Verify the target user exists
        user_result = await db.execute(select(User).filter(User.id == data.user_id))
        analyst = user_result.scalars().first()
        if not analyst:
            raise HTTPException(status_code=404, detail="User not found")

        alert.assigned_to = data.user_id

        # Auto-acknowledge if still OPEN
        if alert.status == AlertStatus.OPEN:
            alert.status = AlertStatus.ACKNOWLEDGED
            alert.acknowledged_at = datetime.now(UTC)

        await db.commit()

        result = await db.execute(_load_alert().filter(FraudAlert.id == alert_id))
        alert = result.scalars().first()
        return _build_detail(alert)
