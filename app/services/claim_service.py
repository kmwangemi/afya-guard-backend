"""
SHA Fraud Detection — Claim Service

Handles: ingestion, provider/member resolution, listing with all UI filters,
         full ClaimDetailResponse assembly for the single-claim view.

Key design notes:
  - All queries that return a serialisable Claim use _load_claim() which
    eager-loads every relationship via selectinload() — prevents MissingGreenlet.
  - list_claims() supports search (ILIKE on claim # OR provider name),
    status, risk_level (subquery on latest FraudScore), county, and pagination.
  - get_claim_detail() assembles the full ClaimDetailResponse that maps 1:1
    to the claim-single.png layout: header, ClaimInformation, FraudAnalysis
    (all four detector blocks), available_actions, and Details timestamps.
"""

import uuid
from datetime import UTC, datetime
from datetime import date as _date
from datetime import datetime as _datetime
from typing import List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.functions import count

from app.models.claim_feature_model import ClaimFeature
from app.models.claim_model import Claim
from app.models.claim_service_model import ClaimService
from app.models.enums_model import AuditAction, ClaimStatus, RiskLevel
from app.models.fraud_score_model import FraudScore
from app.models.member_model import Member
from app.models.provider_model import Provider
from app.schemas.claim_schema import (
    ClaimCreate,
    ClaimDetailResponse,
    ClaimInformation,
    ClaimListFilter,
    ClaimListItem,
    ClaimServiceResponse,
    ClaimStatusUpdate,
    ClaimTimestamps,
    DuplicateClaimAnalysis,
    FraudAnalysis,
    MemberCreate,
    MemberResponse,
    PhantomPatientAnalysis,
    ProviderAnomalyAnalysis,
    ProviderCreate,
    ProviderResponse,
    UpcodingAnalysis,
)
from app.services.audit_service import AuditService


def _to_date(value) -> _date | None:
    """
    Normalise a date/datetime value to a plain date.

    asyncpg returns PostgreSQL DATE columns as timezone-aware datetime objects
    (e.g. datetime(2026, 2, 19, 00:00:00, tzinfo=UTC)) instead of plain date.
    Pydantic v2 rejects these unless the time is exactly midnight, hence the
    ValidationError you're seeing. This helper handles both cases safely.
    """
    if value is None:
        return None
    if isinstance(value, _datetime):
        return value.date()
    if isinstance(value, _date):
        return value
    return None


# ── Reusable eager-load query ─────────────────────────────────────────────────


def _load_claim():
    """
    select(Claim) with all relationships needed for serialisation.
    Use this in every method that returns a Claim to the API layer.
    Avoids MissingGreenlet errors from lazy-loading in async context.
    """
    return select(Claim).options(
        selectinload(Claim.provider),
        selectinload(Claim.member),
        selectinload(Claim.services),
        selectinload(Claim.features),
        selectinload(Claim.fraud_scores).selectinload(FraudScore.explanations),
        selectinload(Claim.fraud_case),
    )


# ── Action resolver ───────────────────────────────────────────────────────────


def _available_actions(claim: Claim, latest_score: Optional[FraudScore]) -> List[str]:
    """
    Return action keys that should render as buttons in the Actions sidebar.
    Matches claim-single.png: Approve, Reject, Create Investigation, Assign.
    """
    terminal = {ClaimStatus.APPROVED, ClaimStatus.REJECTED, ClaimStatus.PAID}
    actions = []

    if claim.sha_status not in terminal:
        actions.append("approve")
        actions.append("reject")

    if (
        latest_score
        and latest_score.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
        and not claim.fraud_case
    ):
        actions.append("create_investigation")

    if claim.sha_status not in terminal:
        actions.append("assign")

    return actions


# ── Fraud analysis builder ────────────────────────────────────────────────────


def _build_fraud_analysis(
    claim: Claim, latest_score: Optional[FraudScore]
) -> FraudAnalysis:
    """
    Build the FraudAnalysis card from the latest FraudScore + its explanations.
    Maps detector names → the four sub-sections in claim-single.png.
    """
    if not latest_score:
        return FraudAnalysis()

    explanations = latest_score.explanations or []
    detector_scores: dict = latest_score.detector_scores or {}

    # ── Phantom Patient ───────────────────────────────────────────────────────
    phantom_score = detector_scores.get("PhantomPatientDetector", 0.0)

    iprs_status = (
        "VERIFIED" if (claim.member and claim.member.national_id) else "UNVERIFIED"
    )

    geographic_anomaly = False
    if claim.member and claim.provider:
        m_county = (claim.member.county or "").upper()
        p_county = (claim.provider.county or "").upper()
        geographic_anomaly = bool(m_county and p_county and m_county != p_county)

    visit_anomaly = bool(
        claim.features
        and claim.features.member_visits_30d
        and claim.features.member_visits_30d > 4
    )

    phantom = PhantomPatientAnalysis(
        detected=phantom_score >= 20,
        iprs_status=iprs_status,
        geographic_anomaly=geographic_anomaly,
        visit_frequency_anomaly=visit_anomaly,
        confidence=round(phantom_score, 1),
    )

    # ── Duplicate Claim ───────────────────────────────────────────────────────
    dup_score = detector_scores.get("DuplicateDetector", 0.0)

    # Pull duplicate claim ids from explanation metadata if stored
    dup_meta_exp = next(
        (
            e
            for e in explanations
            if e.source == "DuplicateDetector" and e.feature_value
        ),
        None,
    )
    dup_count = 0
    if claim.features and claim.features.duplicate_within_7d:
        try:
            dup_count = int(dup_meta_exp.feature_value) if dup_meta_exp else 1
        except (ValueError, TypeError):
            dup_count = 1

    duplicate = DuplicateClaimAnalysis(
        detected=dup_score >= 50,
        duplicate_count=dup_count,
        duplicate_claim_ids=[],
        same_provider=dup_score >= 75,
        window_days=7,
        confidence=round(dup_score, 1),
    )

    # ── Upcoding ──────────────────────────────────────────────────────────────
    up_score = detector_scores.get("UpcodingDetector", 0.0)
    flagged_codes = [
        s.service_code
        for s in (claim.services or [])
        if s.is_upcoded and s.service_code
    ]
    up_reasons = [
        e.explanation
        for e in explanations
        if e.source == "UpcodingDetector" and e.explanation
    ]

    upcoding = UpcodingAnalysis(
        detected=up_score >= 30,
        flagged_service_codes=flagged_codes,
        flag_reasons=up_reasons,
        confidence=round(up_score, 1),
    )

    # ── Provider Anomaly ──────────────────────────────────────────────────────
    prov_score = detector_scores.get("ProviderProfiler", 0.0)
    peer_ratio = None
    if (
        claim.provider
        and claim.provider.avg_claim_amount
        and claim.provider.peer_avg
        and claim.provider.peer_avg > 0
    ):
        peer_ratio = round(claim.provider.avg_claim_amount / claim.provider.peer_avg, 2)

    provider_anomaly = ProviderAnomalyAnalysis(
        detected=prov_score >= 30,
        provider_vs_peer_ratio=peer_ratio,
        high_risk_flag=claim.provider.high_risk_flag if claim.provider else False,
        confidence=round(prov_score, 1),
    )

    # ── Top flags ─────────────────────────────────────────────────────────────
    top_flags = [
        e.explanation
        for e in sorted(explanations, key=lambda x: x.weight or 0, reverse=True)
        if e.weight and e.weight >= 20 and e.explanation
    ][:5]

    return FraudAnalysis(
        overall_score=(
            float(latest_score.final_score) if latest_score.final_score else None
        ),
        risk_level=latest_score.risk_level,
        phantom_patient=phantom,
        duplicate_claim=duplicate,
        upcoding=upcoding,
        provider_anomaly=provider_anomaly,
        top_flags=top_flags,
        rule_score=float(latest_score.rule_score) if latest_score.rule_score else None,
        ml_score=(
            float(latest_score.ml_probability) if latest_score.ml_probability else None
        ),
        detector_scores=detector_scores,
    )


# ══════════════════════════════════════════════════════════════════════════════
# SERVICE
# ══════════════════════════════════════════════════════════════════════════════


class ClaimService_:
    """
    Named ClaimService_ to avoid collision with the ClaimService ORM model.
    Import as: from app.services.claim_service import ClaimService_
    """

    # ── Provider ──────────────────────────────────────────────────────────────

    @staticmethod
    async def create_provider(
        db: AsyncSession, data: ProviderCreate
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
        provider = Provider(**data.model_dump())
        db.add(provider)
        await db.commit()
        await db.refresh(provider)
        return ProviderResponse.model_validate(provider)

    @staticmethod
    async def get_or_create_provider(
        db: AsyncSession, code: str, name: str = "Unknown"
    ) -> Provider:
        result = await db.execute(
            select(Provider).filter(Provider.sha_provider_code == code)
        )
        provider = result.scalars().first()
        if not provider:
            provider = Provider(sha_provider_code=code, name=name)
            db.add(provider)
            await db.commit()
            await db.refresh(provider)
        return provider

    # ── Member ────────────────────────────────────────────────────────────────

    @staticmethod
    async def upsert_member(db: AsyncSession, data: MemberCreate) -> MemberResponse:
        result = await db.execute(
            select(Member).filter(Member.sha_member_id == data.sha_member_id)
        )
        member = result.scalars().first()
        if member:
            for field, value in data.model_dump(exclude_none=True).items():
                setattr(member, field, value)
        else:
            member = Member(**data.model_dump())
            db.add(member)
        await db.commit()
        await db.refresh(member)
        return MemberResponse.model_validate(member)

    @staticmethod
    async def get_or_create_member(db: AsyncSession, sha_member_id: str) -> Member:
        result = await db.execute(
            select(Member).filter(Member.sha_member_id == sha_member_id)
        )
        member = result.scalars().first()
        if not member:
            member = Member(sha_member_id=sha_member_id)
            db.add(member)
            await db.commit()
            await db.refresh(member)
        return member

    # ── Ingest ────────────────────────────────────────────────────────────────

    @staticmethod
    async def ingest_claim(
        db: AsyncSession,
        data: ClaimCreate,
        ingested_by_user_id: Optional[uuid.UUID] = None,
    ) -> Claim:
        # Idempotency
        result = await db.execute(
            select(Claim).filter(Claim.sha_claim_id == data.sha_claim_id)
        )
        if result.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Claim '{data.sha_claim_id}' already ingested",
            )

        # Resolve provider
        p_res = await db.execute(
            select(Provider).filter(Provider.sha_provider_code == data.provider_code)
        )
        provider = p_res.scalars().first()
        if not provider:
            provider = Provider(
                sha_provider_code=data.provider_code,
                name=f"Provider {data.provider_code}",
            )
            db.add(provider)
            await db.flush()

        # Resolve member
        m_res = await db.execute(
            select(Member).filter(Member.sha_member_id == data.member_id_sha)
        )
        member = m_res.scalars().first()
        if not member:
            member = Member(sha_member_id=data.member_id_sha)
            db.add(member)
            await db.flush()

        claim = Claim(
            sha_claim_id=data.sha_claim_id,
            provider_id=provider.id,
            member_id=member.id,
            claim_type=data.claim_type,
            sha_status=data.sha_status,
            admission_date=data.admission_date,
            discharge_date=data.discharge_date,
            diagnosis_codes=data.diagnosis_codes or [],
            total_claim_amount=data.total_claim_amount,
            approved_amount=data.approved_amount,
            submitted_at=data.submitted_at or datetime.now(UTC),
            raw_payload=data.raw_payload or {},
        )
        db.add(claim)
        await db.flush()

        for svc in data.services:
            db.add(
                ClaimService(
                    claim_id=claim.id,
                    service_code=svc.service_code,
                    description=svc.description,
                    quantity=svc.quantity,
                    unit_price=svc.unit_price,
                    total_price=svc.total_price,
                )
            )

        await db.commit()

        # Re-fetch with all relations for serialisation
        result = await db.execute(_load_claim().filter(Claim.id == claim.id))
        claim = result.scalars().first()

        await AuditService.log(
            db,
            AuditAction.CLAIM_INGESTED,
            user_id=ingested_by_user_id,
            entity_type="Claim",
            entity_id=claim.id,
            metadata={
                "sha_claim_id": claim.sha_claim_id,
                "provider_code": data.provider_code,
            },
        )
        return claim

    # ── List  (claim.png — all UI filters) ───────────────────────────────────

    @staticmethod
    async def list_claims(
        db: AsyncSession,
        filters: ClaimListFilter,
        offset: int = 0,
        limit: int = 20,
    ) -> Tuple[List[ClaimListItem], int]:
        """
        Returns (items, total).
        Each ClaimListItem matches one row in the claims table (claim.png):
          Claim # | Provider | Patient ID | Amount | Date | Risk Score | Status

        Supports:
          search     — ILIKE on claim # OR provider name
          sha_status — exact match on claim status
          risk_level — subquery on the claim's latest FraudScore
          county     — ILIKE on provider county
        """
        # Join Provider so we can filter/search on its columns
        q = (
            select(Claim)
            .join(Provider, Claim.provider_id == Provider.id, isouter=True)
            .options(
                selectinload(Claim.provider),
                selectinload(Claim.member),
                selectinload(Claim.services),
                selectinload(Claim.fraud_scores),
            )
        )

        # Search — claim # OR provider name
        if filters.search:
            term = f"%{filters.search.strip()}%"
            q = q.filter(
                or_(
                    Claim.sha_claim_id.ilike(term),
                    Provider.name.ilike(term),
                )
            )

        # Status filter
        if filters.sha_status:
            q = q.filter(Claim.sha_status == filters.sha_status)

        # County filter
        if filters.county:
            q = q.filter(Provider.county.ilike(f"%{filters.county.strip()}%"))

        # Risk level filter — subquery: latest scored_at per claim
        if filters.risk_level:
            latest_sq = (
                select(
                    FraudScore.claim_id,
                    func.max(FraudScore.scored_at).label("latest"),
                )
                .group_by(FraudScore.claim_id)
                .subquery()
            )
            matching_ids_sq = (
                select(FraudScore.claim_id)
                .join(
                    latest_sq,
                    (FraudScore.claim_id == latest_sq.c.claim_id)
                    & (FraudScore.scored_at == latest_sq.c.latest),
                )
                .filter(FraudScore.risk_level == filters.risk_level)
                .subquery()
            )
            q = q.filter(Claim.id.in_(select(matching_ids_sq.c.claim_id)))

        # Advanced filters
        if filters.provider_id:
            q = q.filter(Claim.provider_id == filters.provider_id)
        if filters.member_id:
            q = q.filter(Claim.member_id == filters.member_id)
        if filters.claim_type:
            q = q.filter(Claim.claim_type == filters.claim_type)
        if filters.submitted_from:
            q = q.filter(Claim.submitted_at >= filters.submitted_from)
        if filters.submitted_to:
            q = q.filter(Claim.submitted_at <= filters.submitted_to)
        if filters.min_amount is not None:
            q = q.filter(Claim.total_claim_amount >= filters.min_amount)
        if filters.max_amount is not None:
            q = q.filter(Claim.total_claim_amount <= filters.max_amount)

        # Total count (on filtered query, not full table)
        count_result = await db.execute(select(count()).select_from(q.subquery()))
        total = count_result.scalar_one()

        # Paginated rows
        result = await db.execute(
            q.order_by(Claim.submitted_at.desc()).offset(offset).limit(limit)
        )
        claims = result.scalars().all()

        # Build ClaimListItem rows — pick latest fraud score per claim
        items: List[ClaimListItem] = []
        for claim in claims:
            latest_score = (
                sorted(claim.fraud_scores, key=lambda s: s.scored_at, reverse=True)[0]
                if claim.fraud_scores
                else None
            )

            raw_id = claim.member.sha_member_id if claim.member else None
            masked = f"****{raw_id[-4:]}" if raw_id and len(raw_id) >= 4 else raw_id

            service_date = _to_date(claim.admission_date) or _to_date(
                claim.submitted_at
            )

            items.append(
                ClaimListItem(
                    id=claim.id,
                    sha_claim_id=claim.sha_claim_id,
                    provider_name=claim.provider.name if claim.provider else None,
                    provider_id_code=(
                        claim.provider.sha_provider_code if claim.provider else None
                    ),
                    member_sha_id_masked=masked,
                    total_claim_amount=(
                        float(claim.total_claim_amount)
                        if claim.total_claim_amount
                        else None
                    ),
                    service_date=service_date,
                    risk_score=(
                        float(latest_score.final_score)
                        if latest_score and latest_score.final_score
                        else None
                    ),
                    risk_level=latest_score.risk_level if latest_score else None,
                    status=claim.sha_status,
                )
            )

        return items, total

    # ── Get single claim (raw ORM — for score/feature routes) ─────────────────

    @staticmethod
    async def get_claim(db: AsyncSession, claim_id: uuid.UUID) -> Claim:
        result = await db.execute(_load_claim().filter(Claim.id == claim_id))
        claim = result.scalars().first()
        if not claim:
            raise HTTPException(status_code=404, detail="Claim not found")
        return claim

    @staticmethod
    async def get_claim_by_sha_id(db: AsyncSession, sha_claim_id: str) -> Claim:
        result = await db.execute(
            _load_claim().filter(Claim.sha_claim_id == sha_claim_id)
        )
        claim = result.scalars().first()
        if not claim:
            raise HTTPException(
                status_code=404, detail=f"Claim '{sha_claim_id}' not found"
            )
        return claim

    # ── Get claim detail (assembled response — claim-single.png) ──────────────

    @staticmethod
    async def get_claim_detail(
        db: AsyncSession, claim_id: uuid.UUID
    ) -> ClaimDetailResponse:
        """
        Build the full ClaimDetailResponse that maps 1:1 to claim-single.png:
          header / ClaimInformation / FraudAnalysis / actions / timestamps
        """
        result = await db.execute(_load_claim().filter(Claim.id == claim_id))
        claim = result.scalars().first()
        if not claim:
            raise HTTPException(status_code=404, detail="Claim not found")

        latest_score = (
            sorted(claim.fraud_scores, key=lambda s: s.scored_at, reverse=True)[0]
            if claim.fraud_scores
            else None
        )

        # ── Header ────────────────────────────────────────────────────────────

        service_date = _to_date(claim.admission_date) or _to_date(claim.submitted_at)

        # ── Claim Information card ────────────────────────────────────────────
        raw_id = claim.member.sha_member_id if claim.member else None
        patient_masked = f"****{raw_id[-4:]}" if raw_id and len(raw_id) >= 4 else raw_id

        # Procedure: join service descriptions, fall back to service codes
        procedure_parts = [
            s.description or s.service_code or ""
            for s in (claim.services or [])
            if s.description or s.service_code
        ]
        procedure_str = ", ".join(filter(None, procedure_parts)) or None

        # Diagnosis: join ICD-10 codes as display string
        # In production: replace with a lookup table for human-readable names
        diagnosis_str = (
            ", ".join(claim.diagnosis_codes) if claim.diagnosis_codes else None
        )

        claim_info = ClaimInformation(
            patient_id_masked=patient_masked,
            provider_id_code=(
                claim.provider.sha_provider_code if claim.provider else None
            ),
            provider_name=claim.provider.name if claim.provider else None,
            diagnosis=diagnosis_str,
            diagnosis_codes=claim.diagnosis_codes or [],
            procedure=procedure_str,
            service_date_from=_to_date(claim.admission_date),
            service_date_to=_to_date(claim.discharge_date),
            county=claim.provider.county if claim.provider else None,
        )

        return ClaimDetailResponse(
            id=claim.id,
            sha_claim_id=claim.sha_claim_id,
            provider_name=claim.provider.name if claim.provider else None,
            status=claim.sha_status,
            risk_score=(
                float(latest_score.final_score)
                if latest_score and latest_score.final_score
                else None
            ),
            risk_level=latest_score.risk_level if latest_score else None,
            claim_amount=(
                float(claim.total_claim_amount) if claim.total_claim_amount else None
            ),
            service_date=service_date,
            claim_information=claim_info,
            fraud_analysis=_build_fraud_analysis(claim, latest_score),
            available_actions=_available_actions(claim, latest_score),
            details=ClaimTimestamps(
                submitted=claim.submitted_at,
                created=claim.created_at,
                # last_updated=claim.updated_at,
            ),
            claim_type=claim.claim_type,
            services=[
                ClaimServiceResponse.model_validate(s) for s in (claim.services or [])
            ],
            fraud_score_id=latest_score.id if latest_score else None,
        )

    # ── Update status ─────────────────────────────────────────────────────────

    @staticmethod
    async def update_claim_status(
        db: AsyncSession,
        claim_id: uuid.UUID,
        data: ClaimStatusUpdate,
        updated_by_user_id: Optional[uuid.UUID] = None,
    ) -> Claim:
        result = await db.execute(select(Claim).filter(Claim.id == claim_id))
        claim = result.scalars().first()
        if not claim:
            raise HTTPException(status_code=404, detail="Claim not found")

        old_status = claim.sha_status
        claim.sha_status = data.sha_status
        if data.approved_amount is not None:
            claim.approved_amount = data.approved_amount
        if data.sha_status in (ClaimStatus.APPROVED, ClaimStatus.PAID):
            claim.processed_at = datetime.now(UTC)
        await db.commit()

        # Re-fetch with relations
        result = await db.execute(_load_claim().filter(Claim.id == claim_id))
        claim = result.scalars().first()

        await AuditService.log(
            db,
            AuditAction.CLAIM_STATUS_UPDATED,
            user_id=updated_by_user_id,
            entity_type="Claim",
            entity_id=claim.id,
            metadata={"old_status": old_status, "new_status": data.sha_status},
        )
        return claim

    # ── Features ──────────────────────────────────────────────────────────────

    @staticmethod
    async def get_features(
        db: AsyncSession, claim_id: uuid.UUID
    ) -> Optional[ClaimFeature]:
        result = await db.execute(
            select(ClaimFeature).filter(ClaimFeature.claim_id == claim_id)
        )
        return result.scalars().first()
