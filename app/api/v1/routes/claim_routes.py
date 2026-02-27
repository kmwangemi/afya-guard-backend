"""
SHA Fraud Detection — Claim Routes

POST   /api/v1/claims                           Ingest claim
GET    /api/v1/claims                           List claims (filterable)
GET    /api/v1/claims/{id}                      Get claim detail
PATCH  /api/v1/claims/{id}/status              Update SHA status
GET    /api/v1/claims/{id}/features            Get engineered features
POST   /api/v1/claims/{id}/features/recompute  Re-run feature engineering
POST   /api/v1/claims/{id}/score               Trigger fraud scoring

POST   /api/v1/providers                       Register provider
GET    /api/v1/members/upsert                  Upsert member
"""

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import (
    PaginationParams,
    get_db,
    require_permission,
)
from app.models.enums_model import ClaimStatus
from app.schemas.base_schema import PaginatedResponse
from app.schemas.claim_schema import (
    ClaimCreate,
    ClaimDetailResponse,
    ClaimFeatureResponse,
    ClaimListFilter,
    ClaimResponse,
    ClaimStatusUpdate,
    FraudScoreSlim,
    MemberCreate,
    MemberResponse,
    ProviderCreate,
    ProviderResponse,
)
from app.schemas.fraud_schema import FraudScoreResponse
from app.services.claim_service import ClaimService_
from app.services.feature_service import FeatureService
from app.services.fraud_service import FraudService
from app.schemas.user_schema import UserResponse

router = APIRouter(tags=["Claims"])


# ── Provider ──────────────────────────────────────────────────────────────────


@router.post(
    "/providers",
    response_model=ProviderResponse,
    status_code=201,
    summary="Register a provider",
)
async def create_provider(
    data: ProviderCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("ingest_claim")),
):
    return await ClaimService_.create_provider(db, data)


# ── Member ────────────────────────────────────────────────────────────────────


@router.post(
    "/members",
    response_model=MemberResponse,
    status_code=201,
    summary="Upsert a member record",
)
async def upsert_member(
    data: MemberCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("ingest_claim")),
):
    return await ClaimService_.upsert_member(db, data)


# ── Claims ────────────────────────────────────────────────────────────────────


@router.post(
    "/claims",
    response_model=ClaimResponse,
    status_code=201,
    summary="Ingest a new claim",
)
async def ingest_claim(
    data: ClaimCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("ingest_claim")),
):
    """
    Ingest a claim from SHA (direct submission or webhook handler).
    Triggers feature engineering + fraud scoring as a background task.
    """
    claim = await ClaimService_.ingest_claim(
        db, data, ingested_by_user_id=current_user.id
    )
    async def _score():
        engine = FraudService(db)
        await engine.score_claim(claim, scored_by="system", triggered_by_user_id=None)
    background_tasks.add_task(_score)
    return ClaimResponse.model_validate(claim)


@router.get(
    "/claims",
    response_model=PaginatedResponse[ClaimResponse],
    summary="List claims",
)
async def list_claims(
    provider_id: uuid.UUID = Query(None),
    member_id: uuid.UUID = Query(None),
    sha_status: str = Query(None),
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("view_claim")),
):
    status_filter = ClaimStatus(sha_status) if sha_status else None
    filters = ClaimListFilter(
        provider_id=provider_id,
        member_id=member_id,
        sha_status=status_filter,
    )
    claims, total = await ClaimService_.list_claims(
        db, filters, offset=pagination.offset, limit=pagination.page_size
    )
    return PaginatedResponse(
        items=[ClaimResponse.model_validate(c) for c in claims],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=-(-total // pagination.page_size),
    )


@router.get(
    "/claims/{claim_id}",
    response_model=ClaimDetailResponse,
    summary="Get claim detail",
)
async def get_claim(
    claim_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("view_claim")),
):
    claim = await ClaimService_.get_claim(db, claim_id)
    features = await ClaimService_.get_features(db, claim_id)
    latest_score = claim.fraud_scores[-1] if claim.fraud_scores else None
    result = ClaimDetailResponse.model_validate(claim)
    if features:
        result.features = ClaimFeatureResponse.model_validate(features)
    if latest_score:
        result.latest_fraud_score = FraudScoreSlim(
            id=latest_score.id,
            final_score=(
                float(latest_score.final_score) if latest_score.final_score else None
            ),
            risk_level=latest_score.risk_level,
            scored_at=latest_score.scored_at,
        )
    return result


@router.patch(
    "/claims/{claim_id}/status",
    response_model=ClaimResponse,
    summary="Update claim status",
)
async def update_claim_status(
    claim_id: uuid.UUID,
    data: ClaimStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("update_claim")),
):
    claim = await ClaimService_.update_claim_status(
        db, claim_id, data, updated_by_user_id=current_user.id
    )
    return ClaimResponse.model_validate(claim)


@router.get(
    "/claims/{claim_id}/features",
    response_model=ClaimFeatureResponse,
    summary="Get claim features",
)
async def get_features(
    claim_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("view_features")),
):
    features = await ClaimService_.get_features(db, claim_id)
    if not features:
        raise HTTPException(
            status_code=404,
            detail="Features not yet computed for this claim",
        )
    return ClaimFeatureResponse.model_validate(features)


@router.post(
    "/claims/{claim_id}/features/recompute",
    response_model=ClaimFeatureResponse,
    summary="Recompute features",
)
async def recompute_features(
    claim_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("manage_features")),
):
    claim = await ClaimService_.get_claim(db, claim_id)
    features = await FeatureService.compute_features(
        db, claim, triggered_by=current_user.id
    )
    return ClaimFeatureResponse.model_validate(features)


@router.post(
    "/claims/{claim_id}/score",
    response_model=FraudScoreResponse,
    summary="Trigger fraud scoring",
)
async def score_claim(
    claim_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("score_claim")),
):
    claim = await ClaimService_.get_claim(db, claim_id)
    engine = FraudService(db)
    fraud_score = await engine.score_claim(
        claim,
        scored_by=current_user.email,
        triggered_by_user_id=current_user.id,
    )
    return FraudScoreResponse.model_validate(fraud_score)
