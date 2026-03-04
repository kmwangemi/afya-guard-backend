"""
SHA Fraud Detection — Claim Service

Handles: claim ingestion, provider/member resolution, status updates,
         listing with filters, and feature retrieval.

Fixes applied:
  1. All queries that return a Claim for serialisation now use selectinload()
     on provider, member, and services — prevents MissingGreenlet errors.
  2. _load_claim() helper centralises the eager-load query to avoid repetition.
  3. list_claims() uses func.count() for the total instead of fetching all rows.
"""

import uuid
from datetime import UTC, datetime
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.functions import count

from app.models.claim_feature_model import ClaimFeature
from app.models.claim_model import Claim
from app.models.claim_service_model import ClaimService
from app.models.enums_model import AuditAction, ClaimStatus
from app.models.member_model import Member
from app.models.provider_model import Provider
from app.schemas.claim_schema import (
    ClaimCreate,
    ClaimListFilter,
    ClaimStatusUpdate,
    MemberCreate,
    MemberResponse,
    ProviderCreate,
    ProviderResponse,
)
from app.services.audit_service import AuditService


def _claim_with_relations():
    """
    Reusable select() that eagerly loads all relationships needed
    for ClaimResponse serialisation. Use this everywhere a Claim
    will be returned to the API layer.
    """
    return select(Claim).options(
        selectinload(Claim.provider),
        selectinload(Claim.member),
        selectinload(Claim.services),
    )


class ClaimService_:
    """
    Note: Named ClaimService_ to avoid conflict with the ClaimService ORM model.
    Import as: from app.services.claim_service import ClaimService_
    """

    # ── Provider Helpers ──────────────────────────────────────────────────────

    @staticmethod
    async def get_or_create_provider(
        db: AsyncSession,
        provider_code: str,
        name: str = "Unknown",
    ) -> Provider:
        """Find or create a provider snapshot by SHA provider code."""
        result = await db.execute(
            select(Provider).filter(Provider.sha_provider_code == provider_code)
        )
        provider = result.scalars().first()
        if not provider:
            provider = Provider(sha_provider_code=provider_code, name=name)
            db.add(provider)
            await db.commit()
            await db.refresh(provider)
        return provider

    @staticmethod
    async def create_provider(
        db: AsyncSession,
        data: ProviderCreate,
    ) -> ProviderResponse:
        """Register a new provider, rejecting duplicates."""
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
    async def get_or_create_member(
        db: AsyncSession,
        sha_member_id: str,
    ) -> Member:
        """Find or create a member snapshot by SHA member ID."""
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

    @staticmethod
    async def upsert_member(
        db: AsyncSession,
        data: MemberCreate,
    ) -> MemberResponse:
        """Insert or update a member record."""
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

    # ── Claim Ingestion ───────────────────────────────────────────────────────

    @staticmethod
    async def ingest_claim(
        db: AsyncSession,
        data: ClaimCreate,
        ingested_by_user_id: Optional[uuid.UUID] = None,
    ) -> Claim:
        """
        Ingest a new claim from SHA (via direct POST or webhook).
        Resolves provider + member, stores services and raw payload.
        Does NOT run scoring — that is triggered separately.
        """
        # Idempotency check
        result = await db.execute(
            select(Claim).filter(Claim.sha_claim_id == data.sha_claim_id)
        )
        if result.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Claim '{data.sha_claim_id}' already ingested",
            )

        # Resolve provider
        p_result = await db.execute(
            select(Provider).filter(Provider.sha_provider_code == data.provider_code)
        )
        provider = p_result.scalars().first()
        if not provider:
            provider = Provider(
                sha_provider_code=data.provider_code,
                name=f"Provider {data.provider_code}",
            )
            db.add(provider)
            await db.flush()

        # Resolve member
        m_result = await db.execute(
            select(Member).filter(Member.sha_member_id == data.member_id_sha)
        )
        member = m_result.scalars().first()
        if not member:
            member = Member(sha_member_id=data.member_id_sha)
            db.add(member)
            await db.flush()

        # Create claim
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

        # Create service line items
        for svc in data.services:
            service = ClaimService(
                claim_id=claim.id,
                service_code=svc.service_code,
                description=svc.description,
                quantity=svc.quantity,
                unit_price=svc.unit_price,
                total_price=svc.total_price,
            )
            db.add(service)

        await db.commit()

        # FIX: Re-fetch with eager-loaded relationships so Pydantic
        # can serialise provider, member, and services without hitting
        # the MissingGreenlet error.
        result = await db.execute(_claim_with_relations().filter(Claim.id == claim.id))
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
                "total_amount": data.total_claim_amount,
            },
        )
        return claim

    # ── Read ──────────────────────────────────────────────────────────────────

    @staticmethod
    async def get_claim(
        db: AsyncSession,
        claim_id: uuid.UUID,
    ) -> Claim:
        """Fetch a single claim by internal UUID."""
        # FIX: Use _claim_with_relations() to eager-load provider, member, services
        result = await db.execute(_claim_with_relations().filter(Claim.id == claim_id))
        claim = result.scalars().first()
        if not claim:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Claim not found",
            )
        return claim

    @staticmethod
    async def get_claim_by_sha_id(
        db: AsyncSession,
        sha_claim_id: str,
    ) -> Claim:
        """Fetch a single claim by SHA claim ID."""
        # FIX: Use _claim_with_relations() to eager-load provider, member, services
        result = await db.execute(
            _claim_with_relations().filter(Claim.sha_claim_id == sha_claim_id)
        )
        claim = result.scalars().first()
        if not claim:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Claim '{sha_claim_id}' not found",
            )
        return claim

    @staticmethod
    async def list_claims(
        db: AsyncSession,
        filters: ClaimListFilter,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Claim], int]:
        """List claims with optional filters and pagination."""
        # Base query — without relations first (for count)
        base_query = select(Claim)
        if filters.provider_id:
            base_query = base_query.filter(Claim.provider_id == filters.provider_id)
        if filters.member_id:
            base_query = base_query.filter(Claim.member_id == filters.member_id)
        if filters.sha_status:
            base_query = base_query.filter(Claim.sha_status == filters.sha_status)
        if filters.claim_type:
            base_query = base_query.filter(Claim.claim_type == filters.claim_type)
        if filters.submitted_from:
            base_query = base_query.filter(Claim.submitted_at >= filters.submitted_from)
        if filters.submitted_to:
            base_query = base_query.filter(Claim.submitted_at <= filters.submitted_to)
        if filters.min_amount is not None:
            base_query = base_query.filter(
                Claim.total_claim_amount >= filters.min_amount
            )
        if filters.max_amount is not None:
            base_query = base_query.filter(
                Claim.total_claim_amount <= filters.max_amount
            )

        # FIX: Use a proper COUNT query instead of fetching all rows just to count them.
        # The original code was loading every matching row into memory just to call len().
        # count_result = await db.execute(
        #     select(func.count()).select_from(base_query.subquery())
        # )
        # total = count_result.scalar_one()

        count_result = await db.execute(
            select(count()).select_from(base_query.subquery())
        )
        total = count_result.scalar_one()

        # FIX: Paginated results with eager-loaded relations so serialisation works
        paginated_query = (
            base_query.options(
                selectinload(Claim.provider),
                selectinload(Claim.member),
                selectinload(Claim.services),
            )
            .order_by(Claim.submitted_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await db.execute(paginated_query)
        claims = result.scalars().all()

        return claims, total

    # ── Update ────────────────────────────────────────────────────────────────

    @staticmethod
    async def update_claim_status(
        db: AsyncSession,
        claim_id: uuid.UUID,
        data: ClaimStatusUpdate,
        updated_by_user_id: Optional[uuid.UUID] = None,
    ) -> Claim:
        """Update the SHA status (and optionally approved amount) of a claim."""
        # Fetch without relations first — we only need the claim row to update it
        result = await db.execute(select(Claim).filter(Claim.id == claim_id))
        claim = result.scalars().first()
        if not claim:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Claim not found",
            )

        old_status = claim.sha_status
        claim.sha_status = data.sha_status
        if data.approved_amount is not None:
            claim.approved_amount = data.approved_amount
        if data.sha_status in (ClaimStatus.APPROVED, ClaimStatus.PAID):
            claim.processed_at = datetime.now(UTC)

        await db.commit()

        # FIX: Re-fetch with eager-loaded relationships after commit
        result = await db.execute(_claim_with_relations().filter(Claim.id == claim_id))
        claim = result.scalars().first()

        await AuditService.log(
            db,
            AuditAction.CLAIM_STATUS_UPDATED,
            user_id=updated_by_user_id,
            entity_type="Claim",
            entity_id=claim.id,
            metadata={
                "old_status": old_status,
                "new_status": data.sha_status,
            },
        )
        return claim

    # ── Features ──────────────────────────────────────────────────────────────

    @staticmethod
    async def get_features(
        db: AsyncSession,
        claim_id: uuid.UUID,
    ) -> Optional[ClaimFeature]:
        """Retrieve extracted ML features for a claim."""
        result = await db.execute(
            select(ClaimFeature).filter(ClaimFeature.claim_id == claim_id)
        )
        return result.scalars().first()
