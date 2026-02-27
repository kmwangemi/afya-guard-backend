"""
SHA Fraud Detection — Feature Engineering Service

Transforms raw claim data into ML-ready features stored in claim_features table.
Called automatically after claim ingestion and can be re-triggered manually.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.claim_feature_model import ClaimFeature
from app.models.claim_model import Claim
from app.models.claim_service_model import ClaimService
from app.models.enums_model import AuditAction
from app.services.audit_service import AuditService


class FeatureService:

    @staticmethod
    async def compute_features(
        db: AsyncSession,
        claim: Claim,
        triggered_by: Optional[uuid.UUID] = None,
    ) -> ClaimFeature:
        """
        Compute all engineered features for a claim and persist to claim_features.
        If features already exist for this claim they are updated (re-computation).
        """

        # ── 1. Length of stay ─────────────────────────────────────────────────
        length_of_stay = 0
        if claim.admission_date and claim.discharge_date:
            length_of_stay = max((claim.discharge_date - claim.admission_date).days, 0)
        # ── 2. Weekend submission ─────────────────────────────────────────────
        weekend_submission = False
        if claim.submitted_at:
            weekend_submission = claim.submitted_at.weekday() >= 5  # Sat=5, Sun=6
        # ── 3. Member visit frequency ─────────────────────────────────────────
        now = claim.submitted_at or datetime.now(UTC)
        r30 = await db.execute(
            select(Claim.id).filter(
                Claim.member_id == claim.member_id,
                Claim.id != claim.id,
                Claim.submitted_at >= now - timedelta(days=30),
            )
        )
        member_visits_30d = len(r30.scalars().all())
        r7 = await db.execute(
            select(Claim.id).filter(
                Claim.member_id == claim.member_id,
                Claim.id != claim.id,
                Claim.submitted_at >= now - timedelta(days=7),
            )
        )
        member_visits_7d = len(r7.scalars().all())
        # Unique providers visited by member in 30 days (facility hopping)
        rp = await db.execute(
            select(Claim.provider_id).filter(
                Claim.member_id == claim.member_id,
                Claim.id != claim.id,
                Claim.submitted_at >= now - timedelta(days=30),
            )
        )
        member_unique_providers_30d = len(set(rp.scalars().all()))
        # ── 4. Duplicate detection ────────────────────────────────────────────
        duplicate_within_7d = False
        if claim.diagnosis_codes:
            dup = await db.execute(
                select(Claim.id).filter(
                    Claim.member_id == claim.member_id,
                    Claim.provider_id == claim.provider_id,
                    Claim.id != claim.id,
                    Claim.submitted_at >= now - timedelta(days=7),
                )
            )
            duplicate_within_7d = len(dup.scalars().all()) > 0
        # ── 5. Provider cost z-score (90-day rolling) ─────────────────────────
        prov_result = await db.execute(
            select(Claim.total_claim_amount).filter(
                Claim.provider_id == claim.provider_id,
                Claim.id != claim.id,
                Claim.submitted_at >= now - timedelta(days=90),
                Claim.total_claim_amount.isnot(None),
            )
        )
        prov_amounts = [float(a) for a in prov_result.scalars().all()]
        provider_avg_cost_90d = None
        provider_cost_zscore = None
        if prov_amounts:
            provider_avg_cost_90d = sum(prov_amounts) / len(prov_amounts)
            variance = sum(
                (x - provider_avg_cost_90d) ** 2 for x in prov_amounts
            ) / len(prov_amounts)
            prov_std = variance**0.5
            if prov_std > 0 and claim.total_claim_amount is not None:
                provider_cost_zscore = (
                    claim.total_claim_amount - provider_avg_cost_90d
                ) / prov_std
        # ── 6. Diagnosis cost z-score ─────────────────────────────────────────
        diagnosis_cost_zscore = None
        if claim.diagnosis_codes and claim.total_claim_amount:
            primary_diag = claim.diagnosis_codes[0]
            diag_result = await db.execute(
                select(Claim.total_claim_amount).filter(
                    Claim.diagnosis_codes.contains([primary_diag]),
                    Claim.id != claim.id,
                    Claim.total_claim_amount.isnot(None),
                )
            )
            diag_amounts = [float(a) for a in diag_result.scalars().all()]
            if diag_amounts:
                diag_avg = sum(diag_amounts) / len(diag_amounts)
                diag_var = sum((x - diag_avg) ** 2 for x in diag_amounts) / len(
                    diag_amounts
                )
                diag_std = diag_var**0.5
                if diag_std > 0:
                    diagnosis_cost_zscore = (
                        claim.total_claim_amount - diag_avg
                    ) / diag_std
        # ── 7. Service count ───────────────────────────────────────────────────
        svc_result = await db.execute(
            select(ClaimService.id).filter(ClaimService.claim_id == claim.id)
        )
        service_count = len(svc_result.scalars().all())
        # ── 8. Lab without diagnosis (phantom billing signal) ──────────────────
        has_lab = False
        if claim.services:
            has_lab = any(
                (s.service_code or "").upper()[:3] in {"LAB", "PAT", "MIC"}
                for s in claim.services
            )
        has_lab_without_diagnosis = has_lab and not claim.diagnosis_codes
        # ── 9. Surgery without theatre notes (phantom billing signal) ──────────
        surgical_codes = {"SURG", "OT", "THEATRE", "ANES", "ANAES"}
        has_surgery = False
        if claim.services:
            has_surgery = any(
                any(sc in (s.service_code or "").upper() for sc in surgical_codes)
                for s in claim.services
            )
        has_surgery_without_theatre = has_surgery and length_of_stay == 0
        # ── Persist features ───────────────────────────────────────────────────
        existing = await db.execute(
            select(ClaimFeature).filter(ClaimFeature.claim_id == claim.id)
        )
        existing_feature = existing.scalars().first()
        feature_data = dict(
            claim_id=claim.id,
            provider_avg_cost_90d=provider_avg_cost_90d,
            provider_cost_zscore=provider_cost_zscore,
            member_visits_30d=member_visits_30d,
            member_visits_7d=member_visits_7d,
            member_unique_providers_30d=member_unique_providers_30d,
            duplicate_within_7d=duplicate_within_7d,
            length_of_stay=length_of_stay,
            weekend_submission=weekend_submission,
            diagnosis_cost_zscore=diagnosis_cost_zscore,
            service_count=service_count,
            has_lab_without_diagnosis=has_lab_without_diagnosis,
            has_surgery_without_theatre=has_surgery_without_theatre,
            engineered_at=datetime.now(UTC),
        )
        if existing_feature:
            for k, v in feature_data.items():
                setattr(existing_feature, k, v)
            features = existing_feature
        else:
            features = ClaimFeature(**feature_data)
            db.add(features)
        await db.commit()
        await db.refresh(features)
        await AuditService.log(
            db,
            AuditAction.FEATURES_COMPUTED,
            user_id=triggered_by,
            entity_type="ClaimFeature",
            entity_id=features.id,
            metadata={"claim_id": str(claim.id)},
        )
        return features
