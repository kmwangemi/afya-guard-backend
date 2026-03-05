"""
SHA Fraud Detection — Fraud, Case & Alert Routes

GET    /api/v1/fraud/high-risk              List HIGH/CRITICAL claims
GET    /api/v1/fraud/scores/{score_id}      Get a single fraud score with explanations
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_permission
from app.models.fraud_score_model import FraudScore
from app.schemas.fraud_schema import FraudScoreResponse, HighRiskClaimResponse
from app.schemas.user_schema import UserResponse

router = APIRouter(tags=["Frauds"])


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
