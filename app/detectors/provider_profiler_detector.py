"""
SHA Fraud Detection — Provider Profiler

Profiles a provider's billing behaviour against:
  1. Their own historical average (deviation over time)
  2. Peer group average (same facility type + county)

Fraud pattern: a provider's average claim amount is 300% above peers in the same county,
or a previously normal provider suddenly starts submitting abnormally large claims.
"""

from typing import Dict, Optional

from sqlalchemy import select

from app.detectors.base_detector import BaseDetector, DetectorResult
from app.models.claim_model import Claim, ClaimFeature
from app.models.provider_model import Provider


class ProviderProfiler(BaseDetector):
    """
    Scoring logic:
        - Provider avg > 1.5× peer avg      → +30
        - Provider avg > 2.0× peer avg      → +50 (instead of 30)
        - Provider avg > 3.0× peer avg      → +70
        - Provider high_risk_flag is True   → +30
        - This claim > 3× provider own avg  → +20
        - Provider has < 5 historical claims (new, no profile) → +10 (low confidence)
    """

    async def detect(
        self,
        claim: Claim,
        features: Optional[ClaimFeature] = None,
    ) -> DetectorResult:
        if not claim.provider:
            return DetectorResult(
                detector_name=self.name,
                score=0.0,
                fired=False,
                explanation="Provider record not found — cannot profile",
                feature_name="provider_risk",
                feature_value="unknown",
            )
        provider: Provider = claim.provider
        score: float = 0.0
        flags: list[str] = []
        # ── Peer group comparison ─────────────────────────────────────────────
        peer_avg = await self._get_peer_avg(provider)
        provider_avg = await self._get_provider_historical_avg(provider)
        if peer_avg and provider_avg:
            ratio = provider_avg / peer_avg
            if ratio >= 3.0:
                score += 70.0
                flags.append(
                    f"Provider avg (KES {provider_avg:,.0f}) is {ratio:.1f}× peer avg "
                    f"(KES {peer_avg:,.0f})"
                )
            elif ratio >= 2.0:
                score += 50.0
                flags.append(
                    f"Provider avg (KES {provider_avg:,.0f}) is {ratio:.1f}× peer avg"
                )
            elif ratio >= 1.5:
                score += 30.0
                flags.append(
                    f"Provider avg (KES {provider_avg:,.0f}) is {ratio:.1f}× peer avg"
                )
            # Update provider profile columns for use by MLService
            provider.avg_claim_amount = provider_avg
            provider.peer_avg = peer_avg
            await self.db.commit()
        # ── Provider-level high risk flag ─────────────────────────────────────
        if provider.high_risk_flag:
            score += 30.0
            flags.append("Provider is already flagged as high-risk")
        # ── This claim vs provider's own history ──────────────────────────────
        claim_count = await self._get_provider_claim_count(provider)
        if claim_count < 5:
            score += 10.0
            flags.append(
                f"New provider: only {claim_count} historical claims (low confidence profile)"
            )
        elif provider_avg and claim.total_claim_amount:
            if claim.total_claim_amount > 3 * provider_avg:
                score += 20.0
                flags.append(
                    f"This claim (KES {claim.total_claim_amount:,.0f}) is 3× provider's own avg"
                )
        score = min(score, 100.0)
        fired = score > 0
        explanation = (
            "Provider anomaly signals: " + "; ".join(flags)
            if flags
            else "Provider billing behaviour is within normal range"
        )
        return DetectorResult(
            detector_name=self.name,
            score=round(score, 4),
            fired=fired,
            explanation=explanation,
            feature_name="provider_cost_ratio_vs_peers",
            feature_value=str(
                round(provider_avg / peer_avg, 3)
                if peer_avg and provider_avg
                else "N/A"
            ),
            metadata={
                "sha_provider_code": provider.sha_provider_code,
                "provider_name": provider.name,
                "provider_avg_kes": round(provider_avg, 2) if provider_avg else None,
                "peer_avg_kes": round(peer_avg, 2) if peer_avg else None,
                "high_risk_flag": provider.high_risk_flag,
                "historical_claim_count": claim_count,
                "flags": flags,
            },
        )

    async def explain(
        self,
        claim: Claim,
        features: Optional[ClaimFeature] = None,
    ) -> Dict[str, float]:
        if not claim.provider:
            return {}
        provider = claim.provider
        peer_avg = await self._get_peer_avg(provider)
        provider_avg = await self._get_provider_historical_avg(provider)
        claim_count = await self._get_provider_claim_count(provider)
        ratio = (provider_avg / peer_avg) if peer_avg and provider_avg else 1.0
        return {
            "provider_peer_cost_ratio": round(ratio, 4),
            "provider_high_risk_flag": 1.0 if provider.high_risk_flag else 0.0,
            "provider_claim_count": float(claim_count),
        }

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _get_provider_historical_avg(self, provider: Provider) -> Optional[float]:
        """Calculate provider's own average claim amount across all history."""
        result = await self.db.execute(
            select(Claim.total_claim_amount).filter(
                Claim.provider_id == provider.id,
                Claim.total_claim_amount.isnot(None),
            )
        )
        amounts = [float(a) for a in result.scalars().all()]
        if not amounts:
            return None
        return sum(amounts) / len(amounts)

    async def _get_peer_avg(self, provider: Provider) -> Optional[float]:
        """Average claim amount for providers with same facility_type in same county."""
        query = (
            select(Claim.total_claim_amount)
            .join(Provider, Claim.provider_id == Provider.id)
            .filter(
                Provider.id != provider.id,
                Claim.total_claim_amount.isnot(None),
            )
        )
        if provider.facility_type:
            query = query.filter(Provider.facility_type == provider.facility_type)
        if provider.county:
            query = query.filter(Provider.county == provider.county)
        result = await self.db.execute(query)
        amounts = [float(a) for a in result.scalars().all()]
        if not amounts:
            return None
        return sum(amounts) / len(amounts)

    async def _get_provider_claim_count(self, provider: Provider) -> int:
        """Count total historical claims for a provider."""
        result = await self.db.execute(
            select(Claim.id).filter(Claim.provider_id == provider.id)
        )
        return len(result.scalars().all())
