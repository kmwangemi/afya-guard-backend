"""
SHA Fraud Detection — Fraud, Case & Alert Routes

GET    /api/v1/fraud/high-risk              List HIGH/CRITICAL claims
GET    /api/v1/fraud/scores/{score_id}      Get a single fraud score with explanations

GET    /api/v1/cases                        List cases (filtered)
POST   /api/v1/cases                        Create case manually
GET    /api/v1/cases/{id}                   Get case detail
PATCH  /api/v1/cases/{id}/assign            Assign to analyst
PATCH  /api/v1/cases/{id}/status           Update status
POST   /api/v1/cases/{id}/notes            Add note

GET    /api/v1/alerts                       List alerts
GET    /api/v1/alerts/{id}                  Get alert detail
PATCH  /api/v1/alerts/{id}/acknowledge      Acknowledge alert
PATCH  /api/v1/alerts/{id}/resolve          Resolve alert
PATCH  /api/v1/alerts/{id}/assign           Assign alert
"""

import uuid
from datetime import UTC, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import PaginationParams, get_db, require_permission
from app.models.enums_model import AlertSeverity, AlertStatus, CasePriority, CaseStatus
from app.models.fraud_alert_model import FraudAlert
from app.models.fraud_score_model import FraudScore
from app.schemas.admin_schema import (
    AlertAcknowledgeRequest,
    AlertAssignRequest,
    AlertResolveRequest,
    FraudAlertResponse,
)
from app.schemas.base_schema import PaginatedResponse
from app.schemas.case_schema import (
    CaseAssignRequest,
    CaseListFilter,
    CaseNoteCreate,
    CaseNoteResponse,
    CaseStatusUpdate,
    FraudCaseCreate,
    FraudCaseListResponse,
    FraudCaseResponse,
)
from app.schemas.fraud_schema import FraudScoreResponse, HighRiskClaimResponse
from app.schemas.user_schema import UserResponse
from app.services.case_service import CaseService

router = APIRouter(tags=["Fraud & Cases"])


# ══════════════════════════════════════════════════════════════════════════════
# FRAUD SCORES
# ══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/fraud/high-risk",
    response_model=list[HighRiskClaimResponse],
    summary="List HIGH/CRITICAL risk claims",
)
async def get_high_risk_claims(
    min_score: float = Query(70.0, ge=0, le=100),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("view_score")),
):
    result = await db.execute(
        select(FraudScore)
        .filter(FraudScore.final_score >= min_score)
        .order_by(FraudScore.final_score.desc())
        .limit(limit)
    )
    scores = result.scalars().all()
    results = []
    for s in scores:
        claim = s.claim
        top_explanations = [e.explanation for e in (s.explanations or [])[:3]]
        results.append(
            HighRiskClaimResponse(
                claim_id=s.claim_id,
                sha_claim_id=claim.sha_claim_id if claim else "",
                final_score=float(s.final_score) if s.final_score else None,
                risk_level=s.risk_level,
                provider_name=claim.provider.name if claim and claim.provider else None,
                member_sha_id=(
                    claim.member.sha_member_id if claim and claim.member else None
                ),
                total_claim_amount=(
                    float(claim.total_claim_amount)
                    if claim and claim.total_claim_amount
                    else None
                ),
                scored_at=s.scored_at,
                has_open_case=bool(s.fraud_case),
                top_explanations=top_explanations,
            )
        )
    return results


@router.get(
    "/fraud/scores/{score_id}",
    response_model=FraudScoreResponse,
    summary="Get fraud score detail",
)
async def get_fraud_score(
    score_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("view_score")),
):
    result = await db.execute(select(FraudScore).filter(FraudScore.id == score_id))
    score = result.scalars().first()
    if not score:
        raise HTTPException(status_code=404, detail="Fraud score not found")
    return FraudScoreResponse.model_validate(score)


# ══════════════════════════════════════════════════════════════════════════════
# CASES
# ══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/cases",
    response_model=PaginatedResponse[FraudCaseListResponse],
    summary="List fraud cases",
)
async def list_cases(
    status: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    assigned_to: Optional[uuid.UUID] = Query(None),
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("view_score")),
):
    filters = CaseListFilter(
        status=CaseStatus(status) if status else None,
        priority=CasePriority(priority) if priority else None,
        assigned_to=assigned_to,
    )
    items, total = await CaseService.list_cases(
        db, filters, offset=pagination.offset, limit=pagination.page_size
    )
    return PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=-(-total // pagination.page_size),
    )


@router.post(
    "/cases",
    response_model=FraudCaseResponse,
    status_code=201,
    summary="Create fraud case manually",
)
async def create_case(
    data: FraudCaseCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("create_case")),
):
    return await CaseService.create_case(db, data, created_by=current_user)


@router.get(
    "/cases/{case_id}",
    response_model=FraudCaseResponse,
    summary="Get case detail",
)
async def get_case(
    case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("view_score")),
):
    return await CaseService.get_case(db, case_id)


@router.patch(
    "/cases/{case_id}/assign",
    response_model=FraudCaseResponse,
    summary="Assign case to analyst",
)
async def assign_case(
    case_id: uuid.UUID,
    data: CaseAssignRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("assign_case")),
):
    return await CaseService.assign_case(db, case_id, data, assigned_by=current_user)


@router.patch(
    "/cases/{case_id}/status",
    response_model=FraudCaseResponse,
    summary="Update case status",
)
async def update_case_status(
    case_id: uuid.UUID,
    data: CaseStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("update_case")),
):
    return await CaseService.update_status(db, case_id, data, updated_by=current_user)


@router.post(
    "/cases/{case_id}/notes",
    response_model=CaseNoteResponse,
    status_code=201,
    summary="Add note to case",
)
async def add_case_note(
    case_id: uuid.UUID,
    data: CaseNoteCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("create_case")),
):
    return await CaseService.add_note(db, case_id, data, created_by=current_user)


# ══════════════════════════════════════════════════════════════════════════════
# ALERTS
# ══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/alerts",
    response_model=PaginatedResponse[FraudAlertResponse],
    summary="List fraud alerts",
)
async def list_alerts(
    status: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    assigned_to: Optional[uuid.UUID] = Query(None),
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("view_score")),
):
    query = select(FraudAlert)
    if status:
        query = query.filter(FraudAlert.status == AlertStatus(status))
    if severity:
        query = query.filter(FraudAlert.severity == AlertSeverity(severity))
    if assigned_to:
        query = query.filter(FraudAlert.assigned_to == assigned_to)
    # Total count
    all_result = await db.execute(query)
    total = len(all_result.scalars().all())
    # Paginated results
    paged_result = await db.execute(
        query.order_by(FraudAlert.raised_at.desc())
        .offset(pagination.offset)
        .limit(pagination.page_size)
    )
    alerts = paged_result.scalars().all()
    items = []
    for a in alerts:
        item = FraudAlertResponse.model_validate(a)
        if a.claim:
            item.sha_claim_id = a.claim.sha_claim_id
            item.provider_name = a.claim.provider.name if a.claim.provider else None
        if a.assigned_analyst:
            item.assigned_analyst_name = a.assigned_analyst.full_name
        items.append(item)
    return PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=-(-total // pagination.page_size),
    )


@router.get(
    "/alerts/{alert_id}",
    response_model=FraudAlertResponse,
    summary="Get alert detail",
)
async def get_alert(
    alert_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("view_score")),
):
    result = await db.execute(select(FraudAlert).filter(FraudAlert.id == alert_id))
    alert = result.scalars().first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    response = FraudAlertResponse.model_validate(alert)
    if alert.claim:
        response.sha_claim_id = alert.claim.sha_claim_id
        response.provider_name = (
            alert.claim.provider.name if alert.claim.provider else None
        )
    return response


@router.patch(
    "/alerts/{alert_id}/acknowledge",
    response_model=FraudAlertResponse,
    summary="Acknowledge alert",
)
async def acknowledge_alert(
    alert_id: uuid.UUID,
    body: AlertAcknowledgeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("view_score")),
):
    result = await db.execute(select(FraudAlert).filter(FraudAlert.id == alert_id))
    alert = result.scalars().first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.status = AlertStatus.ACKNOWLEDGED
    alert.acknowledged_at = datetime.now(UTC)
    alert.assigned_to = current_user.id
    await db.commit()
    await db.refresh(alert)
    return FraudAlertResponse.model_validate(alert)


@router.patch(
    "/alerts/{alert_id}/resolve",
    response_model=FraudAlertResponse,
    summary="Resolve alert",
)
async def resolve_alert(
    alert_id: uuid.UUID,
    body: AlertResolveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("update_case")),
):
    result = await db.execute(select(FraudAlert).filter(FraudAlert.id == alert_id))
    alert = result.scalars().first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.status = AlertStatus.RESOLVED
    alert.resolved_at = datetime.now(UTC)
    alert.resolved_by = current_user.id
    alert.resolution_note = body.resolution_note
    alert.is_false_positive = body.is_false_positive
    await db.commit()
    await db.refresh(alert)
    return FraudAlertResponse.model_validate(alert)


@router.patch(
    "/alerts/{alert_id}/assign",
    response_model=FraudAlertResponse,
    summary="Assign alert to analyst",
)
async def assign_alert(
    alert_id: uuid.UUID,
    body: AlertAssignRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("assign_case")),
):
    result = await db.execute(select(FraudAlert).filter(FraudAlert.id == alert_id))
    alert = result.scalars().first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.assigned_to = body.assigned_to
    await db.commit()
    await db.refresh(alert)
    return FraudAlertResponse.model_validate(alert)
