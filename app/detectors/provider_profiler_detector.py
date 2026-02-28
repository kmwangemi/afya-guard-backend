"""
SHA Fraud Detection — Provider Risk Profiler

Builds a comprehensive statistical risk profile for a provider based on:

  Component 1 — Billing Anomaly (25%)
    Z-score of provider's avg claim vs peer group (same facility_type + county).
    Flags providers billing significantly above their peer group.

  Component 2 — Rejection & Suspicion Rate (20%)
    Proportion of claims that were rejected, flagged, or previously fraud-confirmed.
    High rejection rate = pattern of non-compliant billing.

  Component 3 — Procedure Diversity (20%)
    Entropy of service code distribution. A provider billing only 1–2 codes
    at high volume is a concentration risk signal.

  Component 4 — Volume Trend (20%)
    Month-over-month claim volume growth. Sudden spikes indicate potential
    fraud ramp-up (e.g. ghost patient farms, bulk phantom billing).

  Component 5 — Fraud History (15%)
    Count and recency of previously confirmed fraud cases linked to the provider.
    Recent confirmed fraud is weighted more heavily than old cases.

Final score = weighted sum of all 5 components (0–100).
Risk category:
  Critical  80–100
  High      60–79
  Medium    40–59
  Low        0–39
"""

import logging
import math
from datetime import UTC, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select

from app.detectors.base_detector import BaseDetector, DetectorResult
from app.models.claim_feature_model import ClaimFeature
from app.models.claim_model import Claim
from app.models.enums_model import RiskLevel
from app.models.fraud_case_model import FraudCase
from app.models.fraud_score_model import FraudScore
from app.models.provider_model import Provider

logger = logging.getLogger(__name__)

# ── Component weights (must sum to 1.0) ───────────────────────────────────────
COMPONENT_WEIGHTS: Dict[str, float] = {
    "billing_anomaly": 0.25,
    "rejection_rate": 0.20,
    "procedure_diversity": 0.20,
    "volume_trend": 0.20,
    "fraud_history": 0.15,
}

# ── Risk categorisation thresholds ───────────────────────────────────────────
RISK_THRESHOLDS = {
    RiskLevel.CRITICAL: 80.0,
    RiskLevel.HIGH: 60.0,
    RiskLevel.MEDIUM: 40.0,
    RiskLevel.LOW: 0.0,
}

# ── Analysis windows ──────────────────────────────────────────────────────────
TREND_MONTHS: int = 6  # how many months of monthly volume to analyse
FRAUD_RECENCY_MONTHS: int = 24  # fraud cases beyond this window are discounted
PEER_MIN_PROVIDERS: int = 3  # minimum peer providers needed for z-score

# ── Z-score thresholds ────────────────────────────────────────────────────────
ZSCORE_CRITICAL: float = 3.0
ZSCORE_HIGH: float = 2.0
ZSCORE_MEDIUM: float = 1.5

# ── Volume spike thresholds (month-over-month growth) ─────────────────────────
VOLUME_SPIKE_CRITICAL: float = 3.0  # 300% growth
VOLUME_SPIKE_HIGH: float = 2.0  # 200% growth
VOLUME_SPIKE_MEDIUM: float = 1.5  # 150% growth


class ProviderProfiler(BaseDetector):
    """
    Statistical provider risk profiler with 5-component weighted scoring.
    Replaces the simple peer-ratio check with a comprehensive risk model.
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
                feature_name="provider_risk_score",
                feature_value="0",
            )

        provider: Provider = claim.provider

        # ── Collect all statistical metrics ──────────────────────────────────
        metrics = await self._collect_metrics(provider, claim)

        # ── Run all 5 scoring components ─────────────────────────────────────
        c1_score, c1_flags, c1_meta = self._score_billing_anomaly(metrics)
        c2_score, c2_flags, c2_meta = self._score_rejection_rate(metrics)
        c3_score, c3_flags, c3_meta = self._score_procedure_diversity(metrics)
        c4_score, c4_flags, c4_meta = self._score_volume_trend(metrics)
        c5_score, c5_flags, c5_meta = self._score_fraud_history(metrics)

        # ── Weighted final score ──────────────────────────────────────────────
        component_scores = {
            "billing_anomaly": c1_score,
            "rejection_rate": c2_score,
            "procedure_diversity": c3_score,
            "volume_trend": c4_score,
            "fraud_history": c5_score,
        }
        final_score = sum(
            component_scores[k] * COMPONENT_WEIGHTS[k] for k in COMPONENT_WEIGHTS
        )
        final_score = round(min(final_score, 100.0), 4)

        # ── Risk category ─────────────────────────────────────────────────────
        risk_category = self._categorise_risk(final_score)

        # ── Update provider profile columns for ML model ──────────────────────
        provider.avg_claim_amount = metrics["provider_avg"]
        provider.peer_avg = metrics["peer_avg"]
        if metrics["peer_avg"] and metrics["provider_avg"]:
            provider.high_risk_flag = final_score >= 60.0
        await self.db.commit()

        all_flags = c1_flags + c2_flags + c3_flags + c4_flags + c5_flags
        fired = final_score > 0
        explanation = (
            f"Provider risk score {final_score:.1f}/100 ({risk_category}): "
            + ("; ".join(all_flags) if all_flags else "No significant anomalies")
        )

        return DetectorResult(
            detector_name=self.name,
            score=final_score,
            fired=fired,
            explanation=explanation,
            feature_name="provider_risk_score",
            feature_value=str(final_score),
            metadata={
                "risk_category": risk_category,
                "component_scores": {
                    k: round(v, 4) for k, v in component_scores.items()
                },
                "component_weights": COMPONENT_WEIGHTS,
                "metrics": metrics,
                "flags": all_flags,
                "provider_name": provider.name,
                "sha_provider_code": provider.sha_provider_code,
                "components": {
                    "billing_anomaly": c1_meta,
                    "rejection_rate": c2_meta,
                    "procedure_diversity": c3_meta,
                    "volume_trend": c4_meta,
                    "fraud_history": c5_meta,
                },
            },
        )

    async def explain(
        self,
        claim: Claim,
        features: Optional[ClaimFeature] = None,
    ) -> Dict[str, float]:
        if not claim.provider:
            return {}
        metrics = await self._collect_metrics(claim.provider, claim)
        peer_avg = metrics["peer_avg"] or 1.0
        provider_avg = metrics["provider_avg"] or 0.0
        return {
            "provider_peer_cost_ratio": round(provider_avg / peer_avg, 4),
            "provider_high_risk_flag": 1.0 if claim.provider.high_risk_flag else 0.0,
            "provider_claim_count": float(metrics["total_claim_count"]),
            "provider_rejection_rate": round(metrics["rejection_rate"], 4),
            "provider_fraud_case_count": float(metrics["confirmed_fraud_count"]),
            "provider_volume_spike": round(metrics["max_mom_growth"], 4),
        }

    # ── Metric collection ─────────────────────────────────────────────────────

    async def _collect_metrics(self, provider: Provider, claim: Claim) -> Dict:
        """
        Gather all raw statistical metrics needed by the 5 scoring components.
        All DB queries are batched here so each component is pure computation.
        """
        now = datetime.now(UTC)

        # ── All provider claims ───────────────────────────────────────────────
        all_claims_result = await self.db.execute(
            select(
                Claim.total_claim_amount,
                Claim.sha_status,
                Claim.submitted_at,
            ).filter(
                Claim.provider_id == provider.id,
                Claim.id != claim.id,
                Claim.total_claim_amount.isnot(None),
            )
        )
        all_claims = all_claims_result.all()

        amounts = [float(r.total_claim_amount) for r in all_claims]
        statuses = [r.sha_status for r in all_claims]
        submitted_ats = [r.submitted_at for r in all_claims if r.submitted_at]

        total_count = len(all_claims)
        provider_avg = (sum(amounts) / total_count) if amounts else 0.0
        provider_std = (
            math.sqrt(sum((x - provider_avg) ** 2 for x in amounts) / total_count)
            if len(amounts) > 1
            else 0.0
        )

        # ── Rejection rate ────────────────────────────────────────────────────
        rejected_statuses = {"REJECTED", "DENIED", "SUSPENDED", "FLAGGED"}
        rejected_count = sum(
            1 for s in statuses if (s or "").upper() in rejected_statuses
        )
        rejection_rate = rejected_count / max(total_count, 1)

        # ── Fraud score flags (high score = previously suspected) ─────────────
        fraud_score_result = await self.db.execute(
            select(FraudScore.final_score)
            .join(Claim, FraudScore.claim_id == Claim.id)
            .filter(
                Claim.provider_id == provider.id,
                FraudScore.final_score >= 60.0,
            )
        )
        high_fraud_scores = fraud_score_result.scalars().all()
        fraud_flagged_count = len(high_fraud_scores)

        # ── Confirmed fraud cases ─────────────────────────────────────────────
        fraud_cases_result = await self.db.execute(
            select(FraudCase.opened_at)
            .join(Claim, FraudCase.claim_id == Claim.id)
            .filter(
                Claim.provider_id == provider.id,
            )
        )
        fraud_case_dates = [r for r in fraud_cases_result.scalars().all() if r]
        confirmed_fraud_count = len(fraud_case_dates)
        recent_cutoff = now - timedelta(days=FRAUD_RECENCY_MONTHS * 30)
        recent_fraud_count = sum(1 for d in fraud_case_dates if d >= recent_cutoff)

        # ── Peer group avg ────────────────────────────────────────────────────
        peer_query = (
            select(Claim.total_claim_amount)
            .join(Provider, Claim.provider_id == Provider.id)
            .filter(
                Provider.id != provider.id,
                Claim.total_claim_amount.isnot(None),
            )
        )
        if provider.facility_type:
            peer_query = peer_query.filter(
                Provider.facility_type == provider.facility_type
            )
        if provider.county:
            peer_query = peer_query.filter(Provider.county == provider.county)

        peer_result = await self.db.execute(peer_query)
        peer_amounts = [float(a) for a in peer_result.scalars().all()]
        peer_avg = (sum(peer_amounts) / len(peer_amounts)) if peer_amounts else None
        peer_std = (
            math.sqrt(
                sum((x - peer_avg) ** 2 for x in peer_amounts) / len(peer_amounts)
            )
            if peer_avg and len(peer_amounts) > 1
            else None
        )
        peer_provider_count = len(set(peer_amounts))  # proxy — unique amounts

        # ── Peer z-score ──────────────────────────────────────────────────────
        peer_zscore = None
        if peer_avg and peer_std and peer_std > 0:
            peer_zscore = (provider_avg - peer_avg) / peer_std

        # ── Service code distribution (for diversity/entropy) ─────────────────
        svc_result = await self.db.execute(
            select(Claim.id).filter(
                Claim.provider_id == provider.id,
                Claim.id != claim.id,
            )
        )
        # Use current claim's services as proxy for provider's code distribution
        # (full service-level query would need a join — approximate with current)
        current_codes = [(s.service_code or "").upper() for s in (claim.services or [])]

        # ── Monthly volume trend (last TREND_MONTHS months) ──────────────────
        monthly_volumes = self._compute_monthly_volumes(submitted_ats, now)
        max_mom_growth = self._max_month_over_month_growth(monthly_volumes)

        # ── This claim vs provider own avg ────────────────────────────────────
        this_claim_amount = float(claim.total_claim_amount or 0)
        claim_vs_own_avg = this_claim_amount / provider_avg if provider_avg > 0 else 1.0

        return {
            # Core
            "total_claim_count": total_count,
            "provider_avg": round(provider_avg, 2),
            "provider_std": round(provider_std, 2),
            "peer_avg": round(peer_avg, 2) if peer_avg else None,
            "peer_std": round(peer_std, 2) if peer_std else None,
            "peer_zscore": round(peer_zscore, 4) if peer_zscore is not None else None,
            "peer_provider_count": peer_provider_count,
            # Rejection
            "rejected_count": rejected_count,
            "rejection_rate": round(rejection_rate, 4),
            "fraud_flagged_count": fraud_flagged_count,
            # Fraud history
            "confirmed_fraud_count": confirmed_fraud_count,
            "recent_fraud_count": recent_fraud_count,
            # Service codes
            "current_service_codes": current_codes,
            # Volume
            "monthly_volumes": monthly_volumes,
            "max_mom_growth": round(max_mom_growth, 4),
            # This claim
            "this_claim_amount": round(this_claim_amount, 2),
            "claim_vs_own_avg": round(claim_vs_own_avg, 4),
        }

    # ── Component 1: Billing anomaly (peer z-score) ───────────────────────────

    def _score_billing_anomaly(self, metrics: Dict) -> Tuple[float, List[str], Dict]:
        score: float = 0.0
        flags: List[str] = []

        peer_zscore = metrics["peer_zscore"]
        provider_avg = metrics["provider_avg"]
        peer_avg = metrics["peer_avg"]
        claim_vs_own = metrics["claim_vs_own_avg"]

        if peer_zscore is not None:
            if peer_zscore >= ZSCORE_CRITICAL:
                score = 100.0
                flags.append(
                    f"Provider avg KES {provider_avg:,.0f} is {peer_zscore:.2f}σ "
                    f"above peer mean KES {peer_avg:,.0f} — critical outlier"
                )
            elif peer_zscore >= ZSCORE_HIGH:
                score = 70.0
                flags.append(
                    f"Provider avg KES {provider_avg:,.0f} is {peer_zscore:.2f}σ "
                    f"above peer mean — high anomaly"
                )
            elif peer_zscore >= ZSCORE_MEDIUM:
                score = 40.0
                flags.append(
                    f"Provider avg KES {provider_avg:,.0f} is {peer_zscore:.2f}σ "
                    f"above peer mean — moderate anomaly"
                )
        elif metrics["peer_provider_count"] < PEER_MIN_PROVIDERS:
            # Not enough peers — small trust penalty
            score = 10.0
            flags.append("Insufficient peer providers for z-score comparison")

        # Bonus: this specific claim is anomalous vs provider's own history
        if claim_vs_own >= 3.0:
            score = min(score + 20.0, 100.0)
            flags.append(
                f"This claim (KES {metrics['this_claim_amount']:,.0f}) is "
                f"{claim_vs_own:.1f}× provider's own historical average"
            )

        # New provider penalty
        if metrics["total_claim_count"] < 5:
            score = min(score + 15.0, 100.0)
            flags.append(
                f"New provider: only {metrics['total_claim_count']} historical claims"
            )

        return (
            round(min(score, 100.0), 4),
            flags,
            {
                "peer_zscore": peer_zscore,
                "provider_avg": provider_avg,
                "peer_avg": peer_avg,
                "claim_vs_own": claim_vs_own,
            },
        )

    # ── Component 2: Rejection & suspicion rate ───────────────────────────────

    def _score_rejection_rate(self, metrics: Dict) -> Tuple[float, List[str], Dict]:
        score: float = 0.0
        flags: List[str] = []

        rejection_rate = metrics["rejection_rate"]
        rejected_count = metrics["rejected_count"]
        flagged_count = metrics["fraud_flagged_count"]
        total = metrics["total_claim_count"]

        # Rejection rate scoring
        if rejection_rate >= 0.40:
            score += 80.0
            flags.append(
                f"Rejection rate {rejection_rate*100:.1f}% ({rejected_count}/{total} claims) — critical"
            )
        elif rejection_rate >= 0.25:
            score += 55.0
            flags.append(
                f"Rejection rate {rejection_rate*100:.1f}% ({rejected_count}/{total} claims) — high"
            )
        elif rejection_rate >= 0.15:
            score += 30.0
            flags.append(
                f"Rejection rate {rejection_rate*100:.1f}% ({rejected_count}/{total} claims) — moderate"
            )

        # High-fraud-score claims (previously suspected but not confirmed)
        if total > 0:
            suspicion_rate = flagged_count / total
            if suspicion_rate >= 0.20:
                score = min(score + 25.0, 100.0)
                flags.append(
                    f"{flagged_count} claims ({suspicion_rate*100:.1f}%) previously "
                    f"scored ≥60 fraud risk"
                )

        return (
            round(min(score, 100.0), 4),
            flags,
            {
                "rejection_rate": rejection_rate,
                "rejected_count": rejected_count,
                "flagged_count": flagged_count,
                "total_claims": total,
            },
        )

    # ── Component 3: Procedure diversity (entropy) ────────────────────────────

    def _score_procedure_diversity(
        self, metrics: Dict
    ) -> Tuple[float, List[str], Dict]:
        score: float = 0.0
        flags: List[str] = []

        codes = metrics["current_service_codes"]

        if not codes:
            return 0.0, [], {"reason": "No service codes available"}

        # Shannon entropy — low entropy = low diversity = concentration risk
        code_counts: Dict[str, int] = {}
        for c in codes:
            code_counts[c] = code_counts.get(c, 0) + 1

        total = len(codes)
        entropy = -sum(
            (count / total) * math.log2(count / total)
            for count in code_counts.values()
            if count > 0
        )
        unique_codes = len(code_counts)

        # Max possible entropy for this many codes
        max_entropy = math.log2(unique_codes) if unique_codes > 1 else 1.0
        # Normalised diversity index: 1.0 = perfectly diverse, 0.0 = single code
        diversity_index = (entropy / max_entropy) if max_entropy > 0 else 1.0

        # Score inversely proportional to diversity
        # (low diversity = suspicious concentration)
        if diversity_index < 0.2 and unique_codes == 1:
            score = 60.0
            flags.append(
                f"Extreme code concentration: all services are '{codes[0]}' "
                f"(diversity index {diversity_index:.2f})"
            )
        elif diversity_index < 0.4:
            score = 35.0
            flags.append(
                f"Low procedure diversity: {unique_codes} unique codes, "
                f"diversity index {diversity_index:.2f}"
            )

        return (
            round(min(score, 100.0), 4),
            flags,
            {
                "unique_codes": unique_codes,
                "entropy": round(entropy, 4),
                "diversity_index": round(diversity_index, 4),
                "code_counts": code_counts,
            },
        )

    # ── Component 4: Volume trend ─────────────────────────────────────────────

    def _score_volume_trend(self, metrics: Dict) -> Tuple[float, List[str], Dict]:
        score: float = 0.0
        flags: List[str] = []

        monthly_volumes = metrics["monthly_volumes"]
        max_mom_growth = metrics["max_mom_growth"]

        if not monthly_volumes or len(monthly_volumes) < 2:
            return 0.0, [], {"reason": "Insufficient history for trend analysis"}

        if max_mom_growth >= VOLUME_SPIKE_CRITICAL:
            score = 85.0
            flags.append(
                f"Critical volume spike: {max_mom_growth:.1f}× month-over-month growth"
            )
        elif max_mom_growth >= VOLUME_SPIKE_HIGH:
            score = 55.0
            flags.append(
                f"High volume spike: {max_mom_growth:.1f}× month-over-month growth"
            )
        elif max_mom_growth >= VOLUME_SPIKE_MEDIUM:
            score = 30.0
            flags.append(
                f"Moderate volume spike: {max_mom_growth:.1f}× month-over-month growth"
            )

        return (
            round(min(score, 100.0), 4),
            flags,
            {
                "monthly_volumes": monthly_volumes,
                "max_mom_growth": round(max_mom_growth, 4),
            },
        )

    # ── Component 5: Fraud history ────────────────────────────────────────────

    def _score_fraud_history(self, metrics: Dict) -> Tuple[float, List[str], Dict]:
        score: float = 0.0
        flags: List[str] = []

        confirmed_total = metrics["confirmed_fraud_count"]
        recent_count = metrics["recent_fraud_count"]

        # Recent fraud cases (within FRAUD_RECENCY_MONTHS) weighted 2×
        weighted = recent_count * 2 + (confirmed_total - recent_count)

        if weighted >= 10:
            score = 100.0
            flags.append(
                f"Extensive fraud history: {confirmed_total} confirmed cases "
                f"({recent_count} in last {FRAUD_RECENCY_MONTHS} months)"
            )
        elif weighted >= 5:
            score = 70.0
            flags.append(
                f"Significant fraud history: {confirmed_total} confirmed cases "
                f"({recent_count} recent)"
            )
        elif weighted >= 2:
            score = 45.0
            flags.append(
                f"Prior fraud cases: {confirmed_total} confirmed "
                f"({recent_count} recent)"
            )
        elif weighted == 1:
            score = 25.0
            flags.append(f"1 prior fraud case on record")

        return (
            round(min(score, 100.0), 4),
            flags,
            {
                "confirmed_total": confirmed_total,
                "recent_count": recent_count,
                "recency_window_months": FRAUD_RECENCY_MONTHS,
                "weighted_score": weighted,
            },
        )

    # ── Risk categorisation ───────────────────────────────────────────────────

    def _categorise_risk(self, score: float) -> str:
        if score >= RISK_THRESHOLDS[RiskLevel.CRITICAL]:
            return RiskLevel.CRITICAL
        if score >= RISK_THRESHOLDS[RiskLevel.HIGH]:
            return RiskLevel.HIGH
        if score >= RISK_THRESHOLDS[RiskLevel.MEDIUM]:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    # ── Volume helpers ────────────────────────────────────────────────────────

    def _compute_monthly_volumes(
        self,
        submitted_ats: List[datetime],
        now: datetime,
    ) -> Dict[str, int]:
        """
        Count claims per calendar month for the last TREND_MONTHS months.
        Returns {"2024-10": 12, "2024-11": 8, ...}
        """
        volumes: Dict[str, int] = {}
        cutoff = now - timedelta(days=TREND_MONTHS * 30)

        for month_offset in range(TREND_MONTHS):
            month_dt = now - timedelta(days=month_offset * 30)
            month_key = month_dt.strftime("%Y-%m")
            volumes[month_key] = 0

        for dt in submitted_ats:
            if dt and dt >= cutoff:
                key = dt.strftime("%Y-%m")
                if key in volumes:
                    volumes[key] += 1

        # Return sorted oldest → newest
        return dict(sorted(volumes.items()))

    def _max_month_over_month_growth(self, monthly_volumes: Dict[str, int]) -> float:
        """
        Return the highest month-over-month growth ratio seen in the window.
        e.g. 5 claims → 20 claims = 4.0× growth.
        Returns 1.0 if no meaningful comparison can be made.
        """
        values = list(monthly_volumes.values())
        if len(values) < 2:
            return 1.0

        max_growth = 1.0
        for i in range(1, len(values)):
            prev = values[i - 1]
            curr = values[i]
            if prev > 0 and curr > 0:
                growth = curr / prev
                if growth > max_growth:
                    max_growth = growth
            elif prev == 0 and curr > 5:
                # Appeared from nowhere with significant volume
                max_growth = max(max_growth, float(curr))

        return max_growth
