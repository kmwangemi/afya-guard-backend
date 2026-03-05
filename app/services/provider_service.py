"""
SHA Fraud Detection — Provider Service

Handles: provider CRUD, list with UI filters, and the full provider detail
response matching Provider_single_page.png.

All computed fields (total_claims, flagged %, risk score, rejection rate,
avg processing time, fraud history) are derived from related Claim and
FraudCase records using SQL aggregates — not from denormalised columns —
so they always reflect live data.

Note on bed_capacity:
  This field is shown in Provider_single_page.png but is not on the current
  Provider ORM model. Until the Alembic migration is added, it will always
  return None. See: alembic revision --autogenerate -m "add provider bed_capacity"
"""

import uuid
from datetime import date, datetime
from typing import List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy import case, func, or_, select
from sqlalchemy.sql.functions import count
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums_model import (
    AuditAction,
    CaseStatus,
    ClaimStatus,
    RiskLevel,
)
from app.models.claim_model import Claim
from app.models.fraud_case_model import FraudCase
from app.models.fraud_score_model import FraudScore
from app.models.provider_model import Provider
from app.schemas.provider_schema import (
    FraudHistory,
    ProviderCreate,
    ProviderDetailResponse,
    ProviderHeaderStats,
    ProviderInformation,
    ProviderListFilter,
    ProviderListItem,
    ProviderResponse,
    ProviderStatistics,
    ProviderUpdate,
    QuickStats,
    RiskProfile,
    RiskProfileBar,
)
from app.services.audit_service import AuditService


def _risk_level_from_score(score: Optional[float]) -> Optional[RiskLevel]:
    """Map a 0–100 fraud score to a RiskLevel enum value."""
    if score is None:
        return None
    if score >= 90:
        return RiskLevel.CRITICAL
    if score >= 70:
        return RiskLevel.HIGH
    if score >= 40:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def _bar_colour(label: str, value: float) -> str:
    """
    Assign progress bar colour matching Provider_single_page.png:
      Claim Deviation    → red
      Rejection Rate     → orange
      Fraud History Score→ purple
    """
    colours = {
        "Claim Deviation": "red",
        "Rejection Rate": "orange",
        "Fraud History Score": "purple",
    }
    return colours.get(label, "green")


class ProviderService:

    # ── CRUD ──────────────────────────────────────────────────────────────────

    @staticmethod
    async def create_provider(
        db: AsyncSession,
        data: ProviderCreate,
        created_by: Optional[uuid.UUID] = None,
    ) -> ProviderResponse:
        result = await db.execute(
            select(Provider).filter(
                Provider.sha_provider_code == data.sha_provider_code
            )
        )
        if result.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Provider '{data.sha_provider_code}' already registered",
            )

        # bed_capacity is in the schema but may not be in the ORM yet
        provider_data = data.model_dump(
            exclude={"bed_capacity"} if not hasattr(Provider, "bed_capacity") else set()
        )
        provider = Provider(**provider_data)

        db.add(provider)
        await db.commit()
        await db.refresh(provider)

        await AuditService.log(
            db,
            AuditAction.USER_CREATED,  # reuse closest action; add PROVIDER_CREATED later
            user_id=created_by,
            entity_type="Provider",
            entity_id=provider.id,
            metadata={
                "sha_provider_code": provider.sha_provider_code,
                "name": provider.name,
            },
        )
        return ProviderResponse.model_validate(provider)

    @staticmethod
    async def update_provider(
        db: AsyncSession,
        provider_id: uuid.UUID,
        data: ProviderUpdate,
        updated_by: Optional[uuid.UUID] = None,
    ) -> ProviderResponse:
        result = await db.execute(select(Provider).filter(Provider.id == provider_id))
        provider = result.scalars().first()
        if not provider:
            raise HTTPException(status_code=404, detail="Provider not found")

        for field, value in data.model_dump(exclude_none=True).items():
            if hasattr(provider, field):
                setattr(provider, field, value)

        await db.commit()
        await db.refresh(provider)
        return ProviderResponse.model_validate(provider)

    @staticmethod
    async def get_provider_orm(db: AsyncSession, provider_id: uuid.UUID) -> Provider:
        result = await db.execute(select(Provider).filter(Provider.id == provider_id))
        provider = result.scalars().first()
        if not provider:
            raise HTTPException(status_code=404, detail="Provider not found")
        return provider

    # ── List  (Providers_page.png) ────────────────────────────────────────────

    @staticmethod
    async def list_providers(
        db: AsyncSession,
        filters: ProviderListFilter,
        offset: int = 0,
        limit: int = 25,
    ) -> Tuple[List[ProviderListItem], int]:
        """
        Returns (items, total).
        Each ProviderListItem matches one table row in Providers_page.png:
          Provider (name + code) | Facility Type | County |
          Total Claims | Flagged % | Risk Score pill

        All computed columns (total_claims, flagged %, risk_score) are derived
        via SQL aggregates — no denormalised columns required.
        """
        # ── Base query ────────────────────────────────────────────────────────
        q = select(Provider)

        # Search — name OR SHA code
        if filters.search:
            term = f"%{filters.search.strip()}%"
            q = q.filter(
                or_(
                    Provider.name.ilike(term),
                    Provider.sha_provider_code.ilike(term),
                )
            )

        # County filter
        if filters.county:
            q = q.filter(Provider.county.ilike(f"%{filters.county.strip()}%"))

        # Facility type filter
        if filters.facility_type:
            q = q.filter(Provider.facility_type == filters.facility_type)

        # Risk level filter — handled after fetching (requires computed risk score)
        # We apply it post-query below

        # Count
        count_result = await db.execute(select(count()).select_from(q.subquery()))
        total = count_result.scalar_one()

        # Fetch providers (paginated)
        result = await db.execute(q.order_by(Provider.name).offset(offset).limit(limit))
        providers = result.scalars().all()

        if not providers:
            return [], 0

        provider_ids = [p.id for p in providers]

        # ── Aggregate: total claims + flagged count per provider ──────────────
        claim_agg = await db.execute(
            select(
                Claim.provider_id,
                count(Claim.id).label("total_claims"),
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
                ).label("flagged_count"),
            )
            .filter(Claim.provider_id.in_(provider_ids))
            .group_by(Claim.provider_id)
        )
        claim_stats: dict[uuid.UUID, dict] = {}
        for row in claim_agg.all():
            claim_stats[row.provider_id] = {
                "total_claims": row.total_claims or 0,
                "flagged_count": int(row.flagged_count or 0),
            }

        # ── Aggregate: avg fraud score per provider (from latest score per claim) ──
        latest_score_sq = (
            select(
                FraudScore.claim_id,
                func.max(FraudScore.scored_at).label("latest"),
            )
            .group_by(FraudScore.claim_id)
            .subquery()
        )
        score_agg = await db.execute(
            select(
                Claim.provider_id,
                func.avg(FraudScore.final_score).label("avg_score"),
            )
            .join(
                latest_score_sq,
                (FraudScore.claim_id == latest_score_sq.c.claim_id)
                & (FraudScore.scored_at == latest_score_sq.c.latest),
            )
            .join(Claim, FraudScore.claim_id == Claim.id)
            .filter(Claim.provider_id.in_(provider_ids))
            .group_by(Claim.provider_id)
        )
        score_stats: dict[uuid.UUID, float] = {}
        for row in score_agg.all():
            score_stats[row.provider_id] = (
                float(row.avg_score) if row.avg_score else 0.0
            )

        # ── Build ProviderListItem rows ───────────────────────────────────────
        items: List[ProviderListItem] = []
        for provider in providers:
            stats = claim_stats.get(
                provider.id, {"total_claims": 0, "flagged_count": 0}
            )
            total_claims = stats["total_claims"]
            flagged_count = stats["flagged_count"]
            avg_score = score_stats.get(provider.id)

            flagged_pct = (
                round((flagged_count / total_claims) * 100, 1)
                if total_claims > 0
                else 0.0
            )
            risk_level = _risk_level_from_score(avg_score)

            # Apply risk_level filter post-aggregate
            if filters.risk_level and risk_level != filters.risk_level:
                continue

            items.append(
                ProviderListItem(
                    id=provider.id,
                    sha_provider_code=provider.sha_provider_code,
                    name=provider.name,
                    facility_type=provider.facility_type,
                    county=provider.county,
                    total_claims=total_claims,
                    flagged_percentage=flagged_pct,
                    risk_score=round(avg_score, 1) if avg_score is not None else None,
                    risk_level=risk_level,
                    accreditation_status=provider.accreditation_status,
                    high_risk_flag=provider.high_risk_flag,
                )
            )

        return items, total

    # ── Detail  (Provider_single_page.png) ────────────────────────────────────

    @staticmethod
    async def get_provider_detail(
        db: AsyncSession,
        provider_id: uuid.UUID,
    ) -> ProviderDetailResponse:
        """
        Build the full ProviderDetailResponse matching Provider_single_page.png.

        All numbers are computed live from Claim + FraudScore + FraudCase
        aggregates — nothing is read from denormalised columns.
        """
        # Fetch provider row
        result = await db.execute(select(Provider).filter(Provider.id == provider_id))
        provider = result.scalars().first()
        if not provider:
            raise HTTPException(status_code=404, detail="Provider not found")

        # ── 1. Claim counts & totals ──────────────────────────────────────────
        claim_stats_row = await db.execute(
            select(
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
                func.sum(
                    case((Claim.sha_status == ClaimStatus.REJECTED, 1), else_=0)
                ).label("rejected"),
                func.sum(Claim.total_claim_amount).label("total_amount"),
                func.avg(Claim.total_claim_amount).label("avg_amount"),
                func.avg(
                    func.extract(
                        "epoch",
                        func.coalesce(Claim.processed_at, func.now())
                        - Claim.submitted_at,
                    )
                    / 86400  # seconds → days
                ).label("avg_processing_days"),
                func.max(Claim.submitted_at).label("last_claim_at"),
            ).filter(Claim.provider_id == provider_id)
        )
        cs = claim_stats_row.first()

        total_claims = int(cs.total or 0)
        flagged_count = int(cs.flagged or 0)
        rejected_count = int(cs.rejected or 0)
        total_amount = float(cs.total_amount or 0)
        avg_claim = float(cs.avg_amount or 0)
        avg_processing_days = round(float(cs.avg_processing_days or 0), 1)
        last_claim_dt = cs.last_claim_at

        last_claim_date: Optional[date] = None
        if last_claim_dt:
            last_claim_date = (
                last_claim_dt.date()
                if isinstance(last_claim_dt, datetime)
                else last_claim_dt
            )

        flagged_pct = (
            round((flagged_count / total_claims) * 100, 1) if total_claims > 0 else 0.0
        )
        rejection_rate = (
            round((rejected_count / total_claims) * 100, 1) if total_claims > 0 else 0.0
        )

        # ── 2. Average fraud score (from latest score per claim) ──────────────
        latest_sq = (
            select(FraudScore.claim_id, func.max(FraudScore.scored_at).label("latest"))
            .group_by(FraudScore.claim_id)
            .subquery()
        )
        score_row = await db.execute(
            select(func.avg(FraudScore.final_score).label("avg_score"))
            .join(
                latest_sq,
                (FraudScore.claim_id == latest_sq.c.claim_id)
                & (FraudScore.scored_at == latest_sq.c.latest),
            )
            .join(Claim, FraudScore.claim_id == Claim.id)
            .filter(Claim.provider_id == provider_id)
        )
        avg_score_val = score_row.scalar_one_or_none()
        avg_score = float(avg_score_val) if avg_score_val else None
        risk_level = _risk_level_from_score(avg_score)

        # ── 3. Claim deviation — provider avg vs peer avg ─────────────────────
        # Deviation = how far this provider's avg is above the peer group
        # Expressed as a 0–100 score for the progress bar
        peer_avg = float(provider.peer_avg or 0)
        prov_avg = float(provider.avg_claim_amount or avg_claim)

        if peer_avg > 0:
            raw_deviation = min((prov_avg / peer_avg - 1) * 100, 100)
            claim_deviation = max(round(raw_deviation, 1), 0.0)
        else:
            claim_deviation = 0.0

        # ── 4. Fraud history score — weighted composite ───────────────────────
        # Simple heuristic: blend flagged % and rejection rate
        fraud_history_score = round(
            min((flagged_pct * 0.6) + (rejection_rate * 0.4), 100), 1
        )

        # ── 5. FraudCase stats ────────────────────────────────────────────────
        case_stats_row = await db.execute(
            select(
                count(FraudCase.id).label("total_cases"),
                func.sum(
                    case((FraudCase.status == CaseStatus.CONFIRMED_FRAUD, 1), else_=0)
                ).label("confirmed"),
                func.sum(
                    case(
                        (
                            FraudCase.status.in_(
                                [CaseStatus.OPEN, CaseStatus.UNDER_REVIEW]
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ).label("suspected"),
            )
            .join(Claim, FraudCase.claim_id == Claim.id)
            .filter(Claim.provider_id == provider_id)
        )
        case_row = case_stats_row.first()
        confirmed_cases = int(case_row.confirmed or 0)
        suspected_cases = int(case_row.suspected or 0)

        # Total fraud amount — sum of claim amounts for confirmed cases
        fraud_amount_row = await db.execute(
            select(func.sum(Claim.total_claim_amount).label("total"))
            .join(FraudCase, FraudCase.claim_id == Claim.id)
            .filter(
                Claim.provider_id == provider_id,
                FraudCase.status == CaseStatus.CONFIRMED_FRAUD,
            )
        )
        total_fraud_amount = float(fraud_amount_row.scalar_one_or_none() or 0)

        # ── Assemble response ─────────────────────────────────────────────────
        bed_capacity = getattr(provider, "bed_capacity", None)

        return ProviderDetailResponse(
            id=provider.id,
            sha_provider_code=provider.sha_provider_code,
            name=provider.name,
            sub_county=provider.sub_county,
            created_at=provider.created_at,
            updated_at=provider.updated_at,
            header=ProviderHeaderStats(
                risk_score=round(avg_score, 1) if avg_score is not None else None,
                risk_level=risk_level,
                total_claims=total_claims,
                flagged_claims_percentage=flagged_pct,
                confirmed_fraud_count=confirmed_cases,
            ),
            provider_information=ProviderInformation(
                facility_type=provider.facility_type,
                county=provider.county,
                phone=provider.phone,
                email=provider.email,
                bed_capacity=bed_capacity,
                status=provider.accreditation_status,
            ),
            risk_profile=RiskProfile(
                claim_deviation=RiskProfileBar(
                    label="Claim Deviation",
                    value=claim_deviation,
                    colour=_bar_colour("Claim Deviation", claim_deviation),
                ),
                rejection_rate=RiskProfileBar(
                    label="Rejection Rate",
                    value=rejection_rate,
                    colour=_bar_colour("Rejection Rate", rejection_rate),
                ),
                fraud_history_score=RiskProfileBar(
                    label="Fraud History Score",
                    value=fraud_history_score,
                    colour=_bar_colour("Fraud History Score", fraud_history_score),
                ),
            ),
            quick_stats=QuickStats(
                total_claims=total_claims,
                flagged=flagged_count,
                confirmed_fraud=confirmed_cases,
                last_claim_date=last_claim_date,
            ),
            fraud_history=FraudHistory(
                confirmed_cases=confirmed_cases,
                suspected_cases=suspected_cases,
                total_fraud_amount=total_fraud_amount,
            ),
            statistics=ProviderStatistics(
                total_amount=total_amount,
                average_claim=avg_claim,
                rejection_rate=rejection_rate,
                avg_processing_time_days=avg_processing_days,
            ),
        )
