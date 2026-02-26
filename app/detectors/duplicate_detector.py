"""
SHA Fraud Detection — Duplicate Claim Detector

Detects when the same member submits a similar claim (same provider + overlapping
diagnosis codes) within a rolling 7-day window.

Fraud pattern: providers re-submit claims with minor edits hoping they pass a second time,
or members visit multiple branches of the same provider for the same condition.
"""

from datetime import timedelta
from typing import Dict, List, Optional

from sqlalchemy import select

from app.detectors.base_detector import BaseDetector, DetectorResult
from app.models.claim_model import Claim, ClaimFeature


class DuplicateDetector(BaseDetector):
    """
    Scoring logic:
        - 1 duplicate within 7 days       → +50 points
        - 2+ duplicates                   → +75 points (capped at 100)
        - Same provider AND same diagnosis → additional +25 points
    """

    WINDOW_DAYS: int = 7
    BASE_SCORE_PER_DUP: float = 50.0
    SAME_PROVIDER_BONUS: float = 25.0
    MAX_SCORE: float = 100.0

    async def detect(
        self,
        claim: Claim,
        features: Optional[ClaimFeature] = None,
    ) -> DetectorResult:
        duplicates = await self._find_duplicates(claim)
        dup_count = len(duplicates)
        if dup_count == 0:
            return DetectorResult(
                detector_name=self.name,
                score=0.0,
                fired=False,
                explanation="No duplicate claims detected within 7-day window",
                feature_name="duplicate_within_7d",
                feature_value="False",
            )
        # Base score
        score = min(dup_count * self.BASE_SCORE_PER_DUP, self.MAX_SCORE)
        # Check if any dups share the same provider (stronger signal)
        same_provider_dups = [
            d for d in duplicates if d.provider_id == claim.provider_id
        ]
        if same_provider_dups:
            score = min(score + self.SAME_PROVIDER_BONUS, self.MAX_SCORE)
        explanation = (
            f"Found {dup_count} duplicate claim(s) for member "
            f"'{claim.member.sha_member_id if claim.member else claim.member_id}' "
            f"within {self.WINDOW_DAYS} days. "
        )
        if same_provider_dups:
            explanation += f"{len(same_provider_dups)} from the same provider."
        return DetectorResult(
            detector_name=self.name,
            score=round(score, 4),
            fired=True,
            explanation=explanation,
            feature_name="duplicate_within_7d",
            feature_value=str(dup_count),
            metadata={
                "duplicate_claim_ids": [str(d.id) for d in duplicates],
                "duplicate_sha_ids": [d.sha_claim_id for d in duplicates],
                "window_days": self.WINDOW_DAYS,
                "same_provider_count": len(same_provider_dups),
            },
        )

    async def explain(
        self,
        claim: Claim,
        features: Optional[ClaimFeature] = None,
    ) -> Dict[str, float]:
        duplicates = await self._find_duplicates(claim)
        return {
            "duplicate_claim_count_7d": float(len(duplicates)),
            "same_provider_duplicate": float(
                sum(1 for d in duplicates if d.provider_id == claim.provider_id)
            ),
        }

    async def _find_duplicates(self, claim: Claim) -> List[Claim]:
        """Query for claims from same member submitted within the rolling window."""
        if not claim.submitted_at:
            return []
        window_start = claim.submitted_at - timedelta(days=self.WINDOW_DAYS)
        result = await self.db.execute(
            select(Claim).filter(
                Claim.member_id == claim.member_id,
                Claim.id != claim.id,
                Claim.submitted_at >= window_start,
                Claim.submitted_at <= claim.submitted_at,
            )
        )
        candidates = result.scalars().all()
        # Filter further: overlapping diagnosis codes (at least one in common)
        if not claim.diagnosis_codes:
            return candidates  # No diagnosis — flag all same-window same-member claims
        claim_diags = set(claim.diagnosis_codes)
        return [
            c
            for c in candidates
            if c.diagnosis_codes and claim_diags.intersection(set(c.diagnosis_codes))
        ]
