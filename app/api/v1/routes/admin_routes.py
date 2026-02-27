"""
SHA Fraud Detection — Admin & Integration Routes

GET/POST/PATCH  /api/v1/rules/*         Fraud rules management
GET/POST/PATCH  /api/v1/models/*        ML model version management
GET             /api/v1/analytics/*     Dashboard analytics
POST            /api/v1/integration/sha/webhook   SHA event receiver
"""

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_permission
from app.models.claim_model import Claim
from app.models.enums_model import CaseStatus, RiskLevel
from app.models.fraud_case_model import FraudCase
from app.models.fraud_score_model import FraudScore
from app.models.provider_model import Provider
from app.schemas.admin_schema import (
    AnalyticsSummary,
    FraudRuleCreate,
    FraudRuleResponse,
    FraudRuleUpdate,
    ModelDeployResponse,
    ModelVersionCreate,
    ModelVersionResponse,
    ProviderAnalytics,
    RiskDistributionItem,
    RuleToggleResponse,
)
from app.schemas.claim_schema import SHAWebhookEvent, SHAWebhookResponse
from app.schemas.user_schema import UserResponse
from app.services.fraud_service import FraudService
from app.services.rule_model_service import ModelService, RuleService

router = APIRouter(tags=["Admin & Integration"])


# ══════════════════════════════════════════════════════════════════════════════
# FRAUD RULES
# ══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/rules", response_model=list[FraudRuleResponse], summary="List fraud rules"
)
async def list_rules(
    active_only: bool = False,
    category: str = None,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("manage_rules")),
):
    return await RuleService.list_rules(db, active_only=active_only, category=category)


@router.post(
    "/rules",
    response_model=FraudRuleResponse,
    status_code=201,
    summary="Create fraud rule",
)
async def create_rule(
    data: FraudRuleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("manage_rules")),
):
    return await RuleService.create_rule(db, data, created_by=current_user)


@router.get("/rules/{rule_id}", response_model=FraudRuleResponse, summary="Get rule")
async def get_rule(
    rule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("manage_rules")),
):
    return await RuleService.get_rule(db, rule_id)


@router.patch(
    "/rules/{rule_id}", response_model=FraudRuleResponse, summary="Update fraud rule"
)
async def update_rule(
    rule_id: uuid.UUID,
    data: FraudRuleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("manage_rules")),
):
    return await RuleService.update_rule(db, rule_id, data, updated_by=current_user)


@router.patch(
    "/rules/{rule_id}/toggle",
    response_model=RuleToggleResponse,
    summary="Toggle rule active/inactive",
)
async def toggle_rule(
    rule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("manage_rules")),
):
    return await RuleService.toggle_rule(db, rule_id, toggled_by=current_user)


# ══════════════════════════════════════════════════════════════════════════════
# MODEL VERSIONS
# ══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/models", response_model=list[ModelVersionResponse], summary="List model versions"
)
async def list_models(
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("view_models")),
):
    return await ModelService.list_models(db)


@router.post(
    "/models",
    response_model=ModelVersionResponse,
    status_code=201,
    summary="Register model version",
)
async def register_model(
    data: ModelVersionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("deploy_model")),
):
    return await ModelService.register_model(db, data, registered_by=current_user)


@router.get(
    "/models/{model_id}",
    response_model=ModelVersionResponse,
    summary="Get model version",
)
async def get_model(
    model_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("view_models")),
):
    return await ModelService.get_model(db, model_id)


@router.patch(
    "/models/{model_id}/deploy",
    response_model=ModelDeployResponse,
    summary="Deploy model version",
)
async def deploy_model(
    model_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("deploy_model")),
):
    return await ModelService.deploy_model(db, model_id, deployed_by=current_user)


# ══════════════════════════════════════════════════════════════════════════════
# ANALYTICS
# ══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/analytics/summary",
    response_model=AnalyticsSummary,
    summary="Dashboard summary stats",
)
async def analytics_summary(
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("view_analytics")),
):
    # Total claims
    claims_result = await db.execute(select(Claim.id))
    total_claims = len(claims_result.scalars().all())
    # Total scored
    scored_result = await db.execute(select(FraudScore.id))
    total_scored = len(scored_result.scalars().all())
    # High risk
    high_result = await db.execute(
        select(FraudScore.id).filter(FraudScore.risk_level == RiskLevel.HIGH)
    )
    high_risk = len(high_result.scalars().all())
    # Critical
    crit_result = await db.execute(
        select(FraudScore.id).filter(FraudScore.risk_level == RiskLevel.CRITICAL)
    )
    critical = len(crit_result.scalars().all())
    flagged = high_risk + critical
    # Open cases
    open_result = await db.execute(
        select(FraudCase.id).filter(FraudCase.status == CaseStatus.OPEN)
    )
    open_cases = len(open_result.scalars().all())
    # Confirmed fraud
    conf_result = await db.execute(
        select(FraudCase.id).filter(FraudCase.status == CaseStatus.CONFIRMED_FRAUD)
    )
    confirmed = len(conf_result.scalars().all())
    # Average score
    avg_result = await db.execute(select(FraudScore.final_score))
    all_scores = [float(s) for s in avg_result.scalars().all() if s is not None]
    avg_score = sum(all_scores) / len(all_scores) if all_scores else 0.0
    # Estimated savings
    savings_result = await db.execute(
        select(FraudCase.estimated_loss).filter(
            FraudCase.status == CaseStatus.CONFIRMED_FRAUD,
            FraudCase.estimated_loss.isnot(None),
        )
    )
    savings = sum(float(v) for v in savings_result.scalars().all())
    return AnalyticsSummary(
        total_claims=total_claims,
        total_scored=total_scored,
        flagged_count=flagged,
        flagged_percent=round((flagged / max(total_scored, 1)) * 100, 2),
        high_risk_count=high_risk,
        critical_risk_count=critical,
        open_cases=open_cases,
        confirmed_fraud_count=confirmed,
        estimated_savings_kes=savings,
        avg_score=round(avg_score, 2),
    )


@router.get(
    "/analytics/risk-distribution",
    response_model=list[RiskDistributionItem],
    summary="Risk level distribution",
)
async def risk_distribution(
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("view_analytics")),
):
    result = await db.execute(select(FraudScore.risk_level))
    all_levels = result.scalars().all()
    total = len(all_levels) or 1
    # Count per risk level using Python
    counts: dict[str, int] = {}
    for level in all_levels:
        key = level or "UNSCORED"
        counts[key] = counts.get(key, 0) + 1
    return [
        RiskDistributionItem(
            risk_level=level,
            count=count,
            percent=round((count / total) * 100, 2),
        )
        for level, count in counts.items()
    ]


@router.get(
    "/analytics/provider/{provider_id}",
    response_model=ProviderAnalytics,
    summary="Provider fraud analytics",
)
async def provider_analytics(
    provider_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("view_analytics")),
):
    prov_result = await db.execute(select(Provider).filter(Provider.id == provider_id))
    provider = prov_result.scalars().first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    # Total claims for provider
    claims_result = await db.execute(
        select(Claim.id).filter(Claim.provider_id == provider_id)
    )
    total = len(claims_result.scalars().all())
    # Flagged claims (HIGH/CRITICAL scores)
    flagged_result = await db.execute(
        select(FraudScore.id)
        .join(Claim, FraudScore.claim_id == Claim.id)
        .filter(
            Claim.provider_id == provider_id,
            FraudScore.risk_level.in_([RiskLevel.HIGH, RiskLevel.CRITICAL]),
        )
    )
    flagged = len(flagged_result.scalars().all())
    # Average fraud score for provider
    scores_result = await db.execute(
        select(FraudScore.final_score)
        .join(Claim, FraudScore.claim_id == Claim.id)
        .filter(Claim.provider_id == provider_id)
    )
    all_scores = [float(s) for s in scores_result.scalars().all() if s is not None]
    avg_score = sum(all_scores) / len(all_scores) if all_scores else 0.0
    peer_avg = provider.peer_avg or 0.0
    avg_amount = provider.avg_claim_amount or 0.0
    deviation = ((avg_amount - peer_avg) / max(peer_avg, 1)) * 100 if peer_avg else 0.0
    return ProviderAnalytics(
        provider_id=provider.id,
        provider_name=provider.name,
        sha_provider_code=provider.sha_provider_code,
        total_claims=total,
        flagged_claims=flagged,
        avg_score=round(avg_score, 2),
        avg_claim_amount=round(avg_amount, 2),
        peer_avg_amount=round(peer_avg, 2),
        deviation_percent=round(deviation, 2),
        high_risk_flag=provider.high_risk_flag,
    )


# ══════════════════════════════════════════════════════════════════════════════
# SHA INTEGRATION WEBHOOK
# ══════════════════════════════════════════════════════════════════════════════


@router.post(
    "/integration/sha/webhook",
    response_model=SHAWebhookResponse,
    summary="SHA webhook receiver — auto-ingest + score",
)
async def sha_webhook(
    event: SHAWebhookEvent,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Receives CLAIM_SUBMITTED events from the SHA system.
    Looks up the claim, triggers fraud scoring in background,
    and returns the risk score synchronously (if already scored)
    or a 'received' acknowledgement.
    """
    result = await db.execute(
        select(Claim).filter(Claim.sha_claim_id == event.claim_id)
    )
    claim = result.scalars().first()
    if not claim:
        return SHAWebhookResponse(
            claim_id=event.claim_id,
            received=True,
            recommendation="CLAIM_NOT_FOUND — ingest first via POST /claims",
        )
    # If already scored return latest score immediately
    if claim.fraud_scores:
        latest = claim.fraud_scores[-1]
        recommendation = (
            "FLAG_FOR_REVIEW"
            if latest.risk_level in ("HIGH", "CRITICAL")
            else "PROCEED"
        )
        return SHAWebhookResponse(
            claim_id=event.claim_id,
            received=True,
            fraud_score=float(latest.final_score) if latest.final_score else None,
            risk_level=latest.risk_level,
            recommendation=recommendation,
        )
    # Trigger background scoring
    async def _score():
        engine = FraudService(db)
        await engine.score_claim(claim, scored_by="sha_webhook")
    background_tasks.add_task(_score)
    return SHAWebhookResponse(
        claim_id=event.claim_id,
        received=True,
        recommendation="SCORING_IN_PROGRESS",
    )
