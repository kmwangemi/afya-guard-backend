"""
SHA Fraud Detection — Duplicate Claims Detector

Detects claims submitted multiple times for the same service using:
  1. Exact duplicate detection  — MD5 fingerprint match (100% identical)
  2. Fuzzy similarity scoring   — weighted field comparison (>85% = duplicate)

Weighted similarity breakdown (sums to 100%):
  - Patient (member_id)    25%
  - Provider               15%
  - Date (admission)       15%
  - Procedure (services)   20%
  - Amount                 15%
  - Diagnosis              10%

Thresholds:
  - Exact match  (fingerprint)  → score 100, fired = True
  - Fuzzy ≥ 85%                 → score 85+, fired = True
  - Fuzzy 60–84%                → score proportional, fired = True (warning)
  - Fuzzy < 60%                 → score 0, fired = False
"""

import hashlib
import json
from datetime import timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.detectors.base_detector import BaseDetector, DetectorResult
from app.models.claim_feature_model import ClaimFeature
from app.models.claim_model import Claim

# ── Similarity weights (must sum to 1.0) ─────────────────────────────────────
WEIGHTS = {
    "patient": 0.25,
    "provider": 0.15,
    "date": 0.15,
    "procedure": 0.20,
    "amount": 0.15,
    "diagnosis": 0.10,
}

# Threshold above which a claim is flagged as duplicate
DUPLICATE_THRESHOLD: float = 0.85  # 85%
SOFT_DUPLICATE_THRESHOLD: float = 0.60  # 60% — warning level

# How far back to search for potential duplicates (days)
LOOKBACK_DAYS: int = 30

# Amount tolerance — claims within ±5% are considered a match on amount
AMOUNT_TOLERANCE_PCT: float = 0.05


class DuplicateClaimsDetector(BaseDetector):
    """
    Detects exact and near-duplicate claims using fingerprinting
    and weighted fuzzy similarity scoring.
    """

    def __init__(self, db: AsyncSession):
        super().__init__(db)

    # ── Public interface ──────────────────────────────────────────────────────

    async def detect(
        self,
        claim: Claim,
        features: Optional[ClaimFeature] = None,
    ) -> DetectorResult:
        """
        Run duplicate detection on a claim.
        Searches the last LOOKBACK_DAYS of claims for the same member.
        """
        # Step 1: Build fingerprint for this claim
        fingerprint = self._build_fingerprint(claim)

        # Step 2: Fetch candidate claims (same member, last 30 days)
        candidates = await self._fetch_candidates(claim)

        if not candidates:
            return DetectorResult(
                detector_name=self.name,
                score=0.0,
                fired=False,
                explanation="No candidate claims found in lookback window",
                feature_name="duplicate_similarity_score",
                feature_value="0.0",
                metadata={"fingerprint": fingerprint, "candidates_checked": 0},
            )

        # Step 3: Check for exact fingerprint match first
        exact_match = await self._check_exact_match(fingerprint, candidates)
        if exact_match:
            return DetectorResult(
                detector_name=self.name,
                score=100.0,
                fired=True,
                explanation=(
                    f"Exact duplicate detected — claim {exact_match.sha_claim_id} "
                    f"has identical fingerprint (same patient, provider, date, "
                    f"procedures and amount)"
                ),
                feature_name="duplicate_similarity_score",
                feature_value="1.0",
                metadata={
                    "match_type": "exact",
                    "matched_claim_id": str(exact_match.id),
                    "matched_sha_id": exact_match.sha_claim_id,
                    "fingerprint": fingerprint,
                    "candidates_checked": len(candidates),
                },
            )

        # Step 4: Fuzzy similarity scoring against all candidates
        best_score, best_match, breakdown = self._find_best_fuzzy_match(
            claim, candidates
        )

        if best_score >= SOFT_DUPLICATE_THRESHOLD and best_match:
            fired = best_score >= DUPLICATE_THRESHOLD
            final_score = round(best_score * 100, 4)  # scale to 0–100
            level = "duplicate" if fired else "potential duplicate"
            explanation = (
                f"Fuzzy {level} detected — {best_score*100:.1f}% similarity with "
                f"claim {best_match.sha_claim_id}. "
                f"Breakdown: patient={breakdown['patient']*100:.0f}%, "
                f"provider={breakdown['provider']*100:.0f}%, "
                f"date={breakdown['date']*100:.0f}%, "
                f"procedure={breakdown['procedure']*100:.0f}%, "
                f"amount={breakdown['amount']*100:.0f}%, "
                f"diagnosis={breakdown['diagnosis']*100:.0f}%"
            )
            return DetectorResult(
                detector_name=self.name,
                score=final_score,
                fired=fired,
                explanation=explanation,
                feature_name="duplicate_similarity_score",
                feature_value=str(round(best_score, 6)),
                metadata={
                    "match_type": "fuzzy",
                    "similarity_score": round(best_score, 6),
                    "similarity_breakdown": breakdown,
                    "matched_claim_id": str(best_match.id),
                    "matched_sha_id": best_match.sha_claim_id,
                    "fingerprint": fingerprint,
                    "candidates_checked": len(candidates),
                    "threshold": DUPLICATE_THRESHOLD,
                },
            )

        return DetectorResult(
            detector_name=self.name,
            score=0.0,
            fired=False,
            explanation=(
                f"No duplicates found — best similarity was "
                f"{best_score*100:.1f}% (threshold: {DUPLICATE_THRESHOLD*100:.0f}%)"
            ),
            feature_name="duplicate_similarity_score",
            feature_value=str(round(best_score, 6)),
            metadata={
                "fingerprint": fingerprint,
                "candidates_checked": len(candidates),
                "best_score": round(best_score, 6),
            },
        )

    async def explain(
        self,
        claim: Claim,
        features: Optional[ClaimFeature] = None,
    ) -> Dict[str, float]:
        """Return feature weights for explainability dashboard."""
        return {k: v for k, v in WEIGHTS.items()}

    # ── Fingerprinting ────────────────────────────────────────────────────────

    def _build_fingerprint(self, claim: Claim) -> str:
        """
        Build a deterministic MD5 fingerprint from claim fields.
        Identical claims will always produce the same hash.

        Fields included: member_id, provider_id, admission_date,
        sorted service codes, total_claim_amount, sorted diagnosis_codes.
        """
        service_codes = sorted(
            [(s.service_code or "").upper() for s in (claim.services or [])]
        )
        diagnosis_codes = sorted([d.upper() for d in (claim.diagnosis_codes or [])])
        # Round amount to nearest 10 KES to absorb trivial rounding differences
        rounded_amount = round((float(claim.total_claim_amount or 0)) / 10) * 10

        payload = json.dumps(
            {
                "member_id": str(claim.member_id),
                "provider_id": str(claim.provider_id),
                "admission_date": str(claim.admission_date),
                "service_codes": service_codes,
                "amount": rounded_amount,
                "diagnosis_codes": diagnosis_codes,
            },
            sort_keys=True,
        )
        return hashlib.md5(payload.encode()).hexdigest()

    # ── Candidate fetching ────────────────────────────────────────────────────

    async def _fetch_candidates(self, claim: Claim) -> List[Claim]:
        """
        Fetch claims for the same member submitted in the lookback window,
        excluding the current claim itself.
        """
        if not claim.submitted_at:
            return []

        cutoff = claim.submitted_at - timedelta(days=LOOKBACK_DAYS)

        result = await self.db.execute(
            select(Claim).filter(
                Claim.member_id == claim.member_id,
                Claim.id != claim.id,
                Claim.submitted_at >= cutoff,
            )
        )
        return result.scalars().all()

    # ── Exact match ───────────────────────────────────────────────────────────

    async def _check_exact_match(
        self,
        fingerprint: str,
        candidates: List[Claim],
    ) -> Optional[Claim]:
        """
        Check whether any candidate shares the same MD5 fingerprint.
        """
        for candidate in candidates:
            if self._build_fingerprint(candidate) == fingerprint:
                return candidate
        return None

    # ── Fuzzy similarity ──────────────────────────────────────────────────────

    def _find_best_fuzzy_match(
        self,
        claim: Claim,
        candidates: List[Claim],
    ) -> Tuple[float, Optional[Claim], Dict[str, float]]:
        """
        Score every candidate against the claim and return the best match.
        Returns (best_score, best_claim, breakdown_dict).
        """
        best_score = 0.0
        best_match = None
        best_breakdown: Dict[str, float] = {}

        for candidate in candidates:
            score, breakdown = self._similarity_score(claim, candidate)
            if score > best_score:
                best_score = score
                best_match = candidate
                best_breakdown = breakdown

        return best_score, best_match, best_breakdown

    def _similarity_score(
        self,
        a: Claim,
        b: Claim,
    ) -> Tuple[float, Dict[str, float]]:
        """
        Compute weighted similarity between two claims.

        Returns (weighted_total, per_field_scores) where each per_field
        score is 0.0–1.0 representing match quality for that field.
        """
        scores: Dict[str, float] = {
            "patient": self._score_patient(a, b),
            "provider": self._score_provider(a, b),
            "date": self._score_date(a, b),
            "procedure": self._score_procedure(a, b),
            "amount": self._score_amount(a, b),
            "diagnosis": self._score_diagnosis(a, b),
        }

        weighted_total = sum(
            scores[field] * weight for field, weight in WEIGHTS.items()
        )

        return round(weighted_total, 6), scores

    # ── Per-field scorers ─────────────────────────────────────────────────────

    def _score_patient(self, a: Claim, b: Claim) -> float:
        """Exact match on member_id — either 1.0 or 0.0."""
        return 1.0 if a.member_id == b.member_id else 0.0

    def _score_provider(self, a: Claim, b: Claim) -> float:
        """Exact match on provider_id — either 1.0 or 0.0."""
        return 1.0 if a.provider_id == b.provider_id else 0.0

    def _score_date(self, a: Claim, b: Claim) -> float:
        """
        Date proximity scoring:
          - Same day          → 1.0
          - Within 3 days     → 0.75
          - Within 7 days     → 0.50
          - Within 14 days    → 0.25
          - Beyond 14 days    → 0.0
        """
        if not a.admission_date or not b.admission_date:
            return 0.0
        delta = abs((a.admission_date - b.admission_date).days)
        if delta == 0:
            return 1.00
        if delta <= 3:
            return 0.75
        if delta <= 7:
            return 0.50
        if delta <= 14:
            return 0.25
        return 0.0

    def _score_procedure(self, a: Claim, b: Claim) -> float:
        """
        Jaccard similarity on service code sets:
          intersection / union
        e.g. {LAB01, CONSULT01} vs {LAB01, CONSULT02} → 1/3 = 0.33
        """
        codes_a = {(s.service_code or "").upper() for s in (a.services or [])}
        codes_b = {(s.service_code or "").upper() for s in (b.services or [])}

        if not codes_a and not codes_b:
            return 1.0  # both empty — treat as matching
        if not codes_a or not codes_b:
            return 0.0

        intersection = len(codes_a & codes_b)
        union = len(codes_a | codes_b)
        return round(intersection / union, 6)

    def _score_amount(self, a: Claim, b: Claim) -> float:
        """
        Amount proximity scoring.
        Within AMOUNT_TOLERANCE_PCT (5%) → 1.0
        Within 20%                       → 0.5
        Within 50%                       → 0.25
        Beyond 50%                       → 0.0
        """
        amt_a = float(a.total_claim_amount or 0)
        amt_b = float(b.total_claim_amount or 0)

        if amt_a == 0 and amt_b == 0:
            return 1.0
        if amt_a == 0 or amt_b == 0:
            return 0.0

        diff_pct = abs(amt_a - amt_b) / max(amt_a, amt_b)
        if diff_pct <= AMOUNT_TOLERANCE_PCT:
            return 1.00
        if diff_pct <= 0.20:
            return 0.50
        if diff_pct <= 0.50:
            return 0.25
        return 0.0

    def _score_diagnosis(self, a: Claim, b: Claim) -> float:
        """
        Jaccard similarity on diagnosis code sets.
        """
        diag_a = {d.upper() for d in (a.diagnosis_codes or [])}
        diag_b = {d.upper() for d in (b.diagnosis_codes or [])}

        if not diag_a and not diag_b:
            return 1.0
        if not diag_a or not diag_b:
            return 0.0

        intersection = len(diag_a & diag_b)
        union = len(diag_a | diag_b)
        return round(intersection / union, 6)
