"""
SHA Fraud Detection — Claim Routes

POST   /api/v1/providers                        Register a provider
POST   /api/v1/members                          Upsert a member record

POST   /api/v1/claims                           Ingest a new claim
GET    /api/v1/claims                           List claims (search + filter + paginate)
GET    /api/v1/claims/{id}                      Full claim detail (claim-single.png)
PATCH  /api/v1/claims/{id}/status               Update claim status
GET    /api/v1/claims/{id}/features             Get engineered ML features
POST   /api/v1/claims/{id}/features/recompute   Re-run feature engineering
POST   /api/v1/claims/{id}/score                Trigger fraud scoring

FIX (background scoring):
  The original _score() closure captured the request-scoped db session.
  By the time FastAPI runs the background task, that session is already
  closed — the claim object is detached, all relationships are gone, and
  every detector short-circuits to score=0.

  Fix: background task opens its own AsyncSession via get_async_session(),
  re-fetches the claim with _load_claim() (selectinload on all relationships),
  then scores it in that fresh session.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session  # FIX: own session for bg task
from app.core.dependencies import PaginationParams, get_db, require_permission
from app.models.enums_model import ClaimStatus, RiskLevel
from app.models.user_model import User
from app.schemas.base_schema import PaginatedResponse
from app.schemas.claim_schema import (
    ClaimCreate,
    ClaimDetailResponse,
    ClaimFeatureResponse,
    ClaimListFilter,
    ClaimListItem,
    ClaimStatusUpdate,
    MemberCreate,
    MemberResponse,
    ProviderCreate,
    ProviderResponse,
)
from app.schemas.fraud_schema import FraudScoreResponse
from app.services.claim_service import ClaimService_
from app.services.feature_service import FeatureService
from app.services.fraud_service import FraudService

router = APIRouter(tags=["Claims"])


# ── Background scoring task ───────────────────────────────────────────────────


async def _score_claim_background(claim_id: uuid.UUID) -> None:
    """
    Run the full fraud scoring pipeline in an isolated AsyncSession.

    Must NOT reuse the request-scoped db session — that session is closed
    by the time FastAPI executes background tasks. Opening a fresh session
    here ensures:
      1. The session is alive for the full duration of scoring.
      2. _load_claim() can selectinload all relationships (member, provider,
         services) so every detector receives a fully-populated Claim object.
      3. No DetachedInstanceError or MissingGreenlet from lazy-load attempts
         on a closed session.
    """
    async with get_async_session() as db:
        claim = await ClaimService_.get_claim(db, claim_id)
        engine = FraudService(db)
        await engine.score_claim(claim, scored_by="system", triggered_by_user_id=None)


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
    current_user: User = Depends(require_permission("ingest_claim")),
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
    current_user: User = Depends(require_permission("ingest_claim")),
):
    return await ClaimService_.upsert_member(db, data)


# ── List claims ───────────────────────────────────────────────────────────────


@router.get(
    "/claims",
    response_model=PaginatedResponse[ClaimListItem],
    summary="List claims",
    description="""
Returns a paginated list of claims with all filter options visible in the UI.

**Search** (`search`): matches on claim number or provider name (case-insensitive).

**Status** (`status`) — pass the enum value:
- `SUBMITTED` — Pending
- `APPROVED`
- `REJECTED`
- `FLAGGED`
- `UNDER_REVIEW` — Under Investigation
- `PAID`

**Risk Level** (`risk_level`): `LOW` | `MEDIUM` | `HIGH` | `CRITICAL`
Filters against the claim's latest fraud score.

**County** (`county`): partial match on provider county, e.g. `Nairobi`.

**Pagination**: `page` (default 1) and `page_size` (default 20, max 100).
""",
)
async def list_claims(
    search: Optional[str] = Query(
        None,
        description="Search by claim # or provider name",
        examples="CLM-2024-000001",
    ),
    status: Optional[str] = Query(
        None,
        description="Filter by claim status: SUBMITTED | APPROVED | REJECTED | FLAGGED | UNDER_REVIEW | PAID",
        examples="FLAGGED",
    ),
    risk_level: Optional[str] = Query(
        None,
        description="Filter by fraud risk level: LOW | MEDIUM | HIGH | CRITICAL",
        examples="HIGH",
    ),
    county: Optional[str] = Query(
        None,
        description="Filter by provider county (partial match)",
        examples="Nairobi",
    ),
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("view_claim")),
):
    try:
        status_filter = ClaimStatus(status) if status else None
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status '{status}'. "
            f"Valid values: {[s.value for s in ClaimStatus]}",
        )

    try:
        risk_filter = RiskLevel(risk_level) if risk_level else None
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid risk_level '{risk_level}'. "
            f"Valid values: {[r.value for r in RiskLevel]}",
        )

    filters = ClaimListFilter(
        search=search,
        sha_status=status_filter,
        risk_level=risk_filter,
        county=county,
    )

    items, total = await ClaimService_.list_claims(
        db, filters, offset=pagination.offset, limit=pagination.page_size
    )

    return PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=-(-total // pagination.page_size),
    )


# ── Ingest claim ──────────────────────────────────────────────────────────────


@router.post(
    "/claims",
    response_model=ClaimListItem,
    status_code=201,
    summary="Ingest a new claim",
    description="Ingests a claim and triggers feature engineering + fraud scoring as a background task.",
)
async def ingest_claim(
    data: ClaimCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("ingest_claim")),
):
    claim = await ClaimService_.ingest_claim(
        db, data, ingested_by_user_id=current_user.id
    )

    # FIX: pass only the claim_id (a plain UUID — not the ORM object).
    # The background task opens its own session and re-fetches the claim
    # with all relationships loaded. Passing the ORM object itself would
    # give the task a detached instance on a closed session.
    background_tasks.add_task(_score_claim_background, claim.id)

    return ClaimListItem(
        id=claim.id,
        sha_claim_id=claim.sha_claim_id,
        provider_name=claim.provider.name if claim.provider else None,
        provider_id_code=claim.provider.sha_provider_code if claim.provider else None,
        member_sha_id_masked=(
            f"****{claim.member.sha_member_id[-4:]}"
            if claim.member and len(claim.member.sha_member_id) >= 4
            else None
        ),
        total_claim_amount=(
            float(claim.total_claim_amount) if claim.total_claim_amount else None
        ),
        service_date=claim.admission_date
        or (claim.submitted_at.date() if claim.submitted_at else None),
        risk_score=None,
        risk_level=None,
        status=claim.sha_status,
    )


# ── Claim detail ──────────────────────────────────────────────────────────────


@router.get(
    "/claims/{claim_id}",
    response_model=ClaimDetailResponse,
    summary="Get full claim detail",
    description="""
Returns the full single-claim view (claim-single.png):

- **Header**: claim number, provider, status badge, risk score pill, amount, service date
- **Claim Information**: patient ID (masked), provider ID, diagnosis, procedure,
  service date range, county
- **Fraud Analysis**: per-detector results —
  Phantom Patient (IPRS status, geographic anomaly, visit frequency),
  Duplicate Claim, Upcoding, Provider Anomaly
- **available_actions**: list of action keys to render as buttons
  (`approve`, `reject`, `create_investigation`, `assign`)
- **Details**: submitted, created, last_updated timestamps
""",
)
async def get_claim_detail(
    claim_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("view_claim")),
):
    return await ClaimService_.get_claim_detail(db, claim_id)


# ── Update status ─────────────────────────────────────────────────────────────


@router.patch(
    "/claims/{claim_id}/status",
    response_model=ClaimDetailResponse,
    summary="Update claim status (Approve / Reject / Flag)",
)
async def update_claim_status(
    claim_id: uuid.UUID,
    data: ClaimStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("update_claim")),
):
    await ClaimService_.update_claim_status(
        db, claim_id, data, updated_by_user_id=current_user.id
    )
    return await ClaimService_.get_claim_detail(db, claim_id)


# ── Features ──────────────────────────────────────────────────────────────────


@router.get(
    "/claims/{claim_id}/features",
    response_model=ClaimFeatureResponse,
    summary="Get ML features for a claim",
)
async def get_features(
    claim_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("view_features")),
):
    features = await ClaimService_.get_features(db, claim_id)
    if not features:
        raise HTTPException(
            status_code=404,
            detail="Features not yet computed for this claim. "
            "Trigger scoring first via POST /claims/{id}/score",
        )
    return ClaimFeatureResponse.model_validate(features)


@router.post(
    "/claims/{claim_id}/features/recompute",
    response_model=ClaimFeatureResponse,
    summary="Re-run feature engineering",
)
async def recompute_features(
    claim_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("manage_features")),
):
    claim = await ClaimService_.get_claim(db, claim_id)
    features = await FeatureService.compute_features(
        db, claim, triggered_by=current_user.id
    )
    return ClaimFeatureResponse.model_validate(features)


# ── Manual score trigger ──────────────────────────────────────────────────────


@router.post(
    "/claims/{claim_id}/score",
    response_model=FraudScoreResponse,
    summary="Trigger fraud scoring",
    description="Manually run the full scoring pipeline (rule engine + ML + detectors).",
)
async def score_claim(
    claim_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("score_claim")),
):
    # get_claim() uses _load_claim() — all relationships are selectinloaded.
    # This route was already correct; no change needed here.
    claim = await ClaimService_.get_claim(db, claim_id)
    engine = FraudService(db)
    fraud_score = await engine.score_claim(
        claim,
        scored_by=current_user.email,
        triggered_by_user_id=current_user.id,
    )
    return FraudScoreResponse.model_validate(fraud_score)
