"""
SHA Fraud Detection — Ghost Provider Detector — PATCHED

Changes vs original:
  [FIX 3] Signal 1 now checks both provider.phone_number and provider.phone
          to handle providers ingested with either field name convention.
  [FIX 4] Signal 5 now uses is_inpatient_code() so "WARD-GEN-DAY" correctly
          triggers the zero-stay check (was silently missed with exact set match).
"""

import logging
import math
from datetime import UTC, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select

from app.detectors.base_detector import BaseDetector, DetectorResult
from app.detectors.upcoding_medical_db import is_inpatient_code  # FIX 4
from app.models.claim_feature_model import ClaimFeature
from app.models.claim_model import Claim
from app.models.provider_model import Provider

logger = logging.getLogger(__name__)

SIGNAL_WEIGHTS: Dict[str, float] = {
    "unverified_presence": 0.25,
    "eligibility_bypass": 0.20,
    "off_hours_submissions": 0.15,
    "member_churning": 0.15,
    "zero_stay_inpatient": 0.10,
    "amount_uniformity": 0.05,
    "new_provider_high_volume": 0.05,
    "claim_discontinuity": 0.05,
}

OFF_HOURS_THRESHOLD: float = 0.40
ELIGIBILITY_SKIP_THRESHOLD: float = 0.70
CHURN_WINDOW_DAYS: int = 30
CHURN_PROVIDER_THRESHOLD: int = 3
NEW_PROVIDER_DAYS: int = 90
NEW_PROVIDER_DAILY_VOLUME: float = 5.0
AMOUNT_CV_THRESHOLD: float = 0.05
LOOKBACK_DAYS: int = 90


class GhostProviderDetector(BaseDetector):

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
                explanation="Provider record not found",
                feature_name="ghost_provider_score",
                feature_value="0",
            )

        provider: Provider = claim.provider
        metrics = await self._collect_metrics(provider, claim, features)

        s1_score, s1_flags, s1_meta = self._signal_unverified_presence(provider)
        s2_score, s2_flags, s2_meta = self._signal_eligibility_bypass(metrics)
        s3_score, s3_flags, s3_meta = self._signal_off_hours(metrics)
        s4_score, s4_flags, s4_meta = self._signal_member_churning(metrics)
        s5_score, s5_flags, s5_meta = self._signal_zero_stay_inpatient(claim, features)
        s6_score, s6_flags, s6_meta = self._signal_amount_uniformity(metrics)
        s7_score, s7_flags, s7_meta = self._signal_new_provider_volume(
            provider, metrics
        )
        s8_score, s8_flags, s8_meta = self._signal_claim_discontinuity(metrics)

        signal_scores = {
            "unverified_presence": s1_score,
            "eligibility_bypass": s2_score,
            "off_hours_submissions": s3_score,
            "member_churning": s4_score,
            "zero_stay_inpatient": s5_score,
            "amount_uniformity": s6_score,
            "new_provider_high_volume": s7_score,
            "claim_discontinuity": s8_score,
        }

        final_score = sum(signal_scores[k] * SIGNAL_WEIGHTS[k] for k in SIGNAL_WEIGHTS)
        final_score = round(min(final_score, 100.0), 4)
        fired = final_score > 0

        all_flags = (
            s1_flags
            + s2_flags
            + s3_flags
            + s4_flags
            + s5_flags
            + s6_flags
            + s7_flags
            + s8_flags
        )
        explanation = (
            f"Ghost provider signals detected (score {final_score:.1f}/100): "
            + ("; ".join(all_flags) if all_flags else "No significant signals")
        )

        if final_score >= 80.0:
            provider.high_risk_flag = True
            await self.db.commit()

        return DetectorResult(
            detector_name=self.name,
            score=final_score,
            fired=fired,
            explanation=explanation,
            feature_name="ghost_provider_score",
            feature_value=str(final_score),
            metadata={
                "signal_scores": {k: round(v, 4) for k, v in signal_scores.items()},
                "signal_weights": SIGNAL_WEIGHTS,
                "flags": all_flags,
                "metrics": metrics,
                "signals": {
                    "unverified_presence": s1_meta,
                    "eligibility_bypass": s2_meta,
                    "off_hours_submissions": s3_meta,
                    "member_churning": s4_meta,
                    "zero_stay_inpatient": s5_meta,
                    "amount_uniformity": s6_meta,
                    "new_provider_high_volume": s7_meta,
                    "claim_discontinuity": s8_meta,
                },
                "provider_name": provider.name,
                "sha_provider_code": provider.sha_provider_code,
            },
        )

    async def explain(
        self,
        claim: Claim,
        features: Optional[ClaimFeature] = None,
    ) -> Dict[str, float]:
        if not claim.provider:
            return {}
        metrics = await self._collect_metrics(claim.provider, claim, features)
        return {
            "eligibility_skip_rate": round(metrics["eligibility_skip_rate"], 4),
            "off_hours_rate": round(metrics["off_hours_rate"], 4),
            "churned_member_count": float(metrics["churned_member_count"]),
            "zero_stay_inpatient_rate": round(metrics["zero_stay_inpatient_rate"], 4),
            "amount_cv": round(metrics["amount_cv"], 4),
            "claims_per_day": round(metrics["claims_per_day"], 4),
        }

    # ── Metric collection ─────────────────────────────────────────────────────

    async def _collect_metrics(
        self,
        provider: Provider,
        claim: Claim,
        features: Optional[ClaimFeature] = None,
    ) -> Dict:
        now = datetime.now(UTC)
        cutoff = now - timedelta(days=LOOKBACK_DAYS)

        result = await self.db.execute(
            select(
                Claim.id,
                Claim.total_claim_amount,
                Claim.submitted_at,
                Claim.member_id,
            ).filter(
                Claim.provider_id == provider.id,
                Claim.id != claim.id,
                Claim.submitted_at >= cutoff,
            )
        )
        recent_claims = result.all()
        total_recent = len(recent_claims)

        feat_result = await self.db.execute(
            select(
                ClaimFeature.eligibility_checked,
                ClaimFeature.submitted_hour,
            )
            .join(Claim, ClaimFeature.claim_id == Claim.id)
            .filter(
                Claim.provider_id == provider.id,
                Claim.id != claim.id,
                Claim.submitted_at >= cutoff,
            )
        )
        feature_rows = feat_result.all()
        total_with_features = len(feature_rows)
        not_checked = sum(1 for r in feature_rows if not r.eligibility_checked)
        eligibility_skip_rate = (
            not_checked / total_with_features if total_with_features > 0 else 0.0
        )

        off_hours = sum(
            1
            for r in feature_rows
            if r.submitted_hour is not None
            and (r.submitted_hour >= 22 or r.submitted_hour <= 5)
        )
        off_hours_rate = (
            off_hours / total_with_features if total_with_features > 0 else 0.0
        )

        amounts = [
            float(r.total_claim_amount) for r in recent_claims if r.total_claim_amount
        ]
        amount_mean = sum(amounts) / len(amounts) if amounts else 0.0
        amount_std = (
            math.sqrt(sum((x - amount_mean) ** 2 for x in amounts) / len(amounts))
            if len(amounts) > 1
            else 0.0
        )
        amount_cv = (amount_std / amount_mean) if amount_mean > 0 else 1.0

        member_ids = {r.member_id for r in recent_claims if r.member_id}
        churned_member_count = 0
        if member_ids:
            churn_cutoff = now - timedelta(days=CHURN_WINDOW_DAYS)
            for member_id in member_ids:
                other_result = await self.db.execute(
                    select(Claim.provider_id)
                    .filter(
                        Claim.member_id == member_id,
                        Claim.provider_id != provider.id,
                        Claim.submitted_at >= churn_cutoff,
                    )
                    .distinct()
                )
                if len(other_result.scalars().all()) >= CHURN_PROVIDER_THRESHOLD:
                    churned_member_count += 1

        # FIX 4: use is_inpatient_code() so WARD-GEN-DAY counts
        inpatient_services = [
            s
            for s in (claim.services or [])
            if is_inpatient_code((s.service_code or "").upper())
        ]
        los = float(features.length_of_stay or 0) if features else 0.0
        has_zero_stay_inpatient = len(inpatient_services) > 0 and los == 0
        zero_stay_inpatient_rate = 1.0 if has_zero_stay_inpatient else 0.0

        days_active = max(
            (
                (
                    now - min(r.submitted_at for r in recent_claims if r.submitted_at)
                ).days
                if recent_claims
                else 1
            ),
            1,
        )
        claims_per_day = total_recent / days_active

        monthly_volumes: Dict[str, int] = {}
        for r in recent_claims:
            if r.submitted_at:
                key = r.submitted_at.strftime("%Y-%m")
                monthly_volumes[key] = monthly_volumes.get(key, 0) + 1

        return {
            "total_recent_claims": total_recent,
            "eligibility_skip_rate": round(eligibility_skip_rate, 4),
            "off_hours_rate": round(off_hours_rate, 4),
            "amount_cv": round(amount_cv, 4),
            "amount_mean": round(amount_mean, 2),
            "amount_std": round(amount_std, 2),
            "unique_member_count": len(member_ids),
            "churned_member_count": churned_member_count,
            "zero_stay_inpatient_rate": zero_stay_inpatient_rate,
            "claims_per_day": round(claims_per_day, 4),
            "monthly_volumes": dict(sorted(monthly_volumes.items())),
        }

    # ── Signal 1: Unverified physical presence — FIX 3 ───────────────────────

    def _signal_unverified_presence(
        self, provider: Provider
    ) -> Tuple[float, List[str], Dict]:
        score: float = 0.0
        flags: List[str] = []
        missing = []

        if not getattr(provider, "latitude", None) or not getattr(
            provider, "longitude", None
        ):
            score += 40.0
            missing.append("geo-coordinates")

        if not getattr(provider, "address", None):
            score += 25.0
            missing.append("physical address")

        # FIX 3: check both "phone_number" (ORM column) and "phone" (ingestion alias)
        has_phone = getattr(provider, "phone_number", None) or getattr(
            provider, "phone", None
        )
        if not has_phone:
            score += 20.0
            missing.append("phone number")

        if not getattr(provider, "license_number", None):
            score += 15.0
            missing.append("license number")

        if missing:
            flags.append(f"Provider missing verifiable fields: {', '.join(missing)}")

        return round(min(score, 100.0), 4), flags, {"missing_fields": missing}

    # ── Signal 2: Eligibility bypass ─────────────────────────────────────────

    def _signal_eligibility_bypass(
        self, metrics: Dict
    ) -> Tuple[float, List[str], Dict]:
        rate = metrics["eligibility_skip_rate"]
        total = metrics["total_recent_claims"]
        if rate >= 0.90:
            score, label = 100.0, "critical"
        elif rate >= ELIGIBILITY_SKIP_THRESHOLD:
            score, label = 65.0, "high"
        elif rate >= 0.40:
            score, label = 35.0, "moderate"
        else:
            return 0.0, [], {"eligibility_skip_rate": rate}
        flags = [
            f"Eligibility bypass rate {rate*100:.1f}% ({label}) — claims submitted without eligibility check"
        ]
        return round(score, 4), flags, {"eligibility_skip_rate": rate, "total": total}

    # ── Signal 3: Off-hours submissions ───────────────────────────────────────

    def _signal_off_hours(self, metrics: Dict) -> Tuple[float, List[str], Dict]:
        rate = metrics["off_hours_rate"]
        if rate >= 0.70:
            score, label = 90.0, "critical"
        elif rate >= OFF_HOURS_THRESHOLD:
            score, label = 55.0, "high"
        elif rate >= 0.20:
            score, label = 25.0, "moderate"
        else:
            return 0.0, [], {"off_hours_rate": rate}
        flags = [
            f"Off-hours submission rate {rate*100:.1f}% ({label}) — bulk claims 22:00–05:00"
        ]
        return round(score, 4), flags, {"off_hours_rate": rate}

    # ── Signal 4: Member churning ─────────────────────────────────────────────

    def _signal_member_churning(self, metrics: Dict) -> Tuple[float, List[str], Dict]:
        churned = metrics["churned_member_count"]
        total = max(metrics["unique_member_count"], 1)
        rate = churned / total
        if rate >= 0.50:
            score, label = 90.0, "critical"
        elif rate >= 0.30:
            score, label = 60.0, "high"
        elif rate >= 0.15:
            score, label = 30.0, "moderate"
        else:
            return 0.0, [], {"churned_member_count": churned, "churn_rate": rate}
        flags = [
            f"Member churning {label}: {churned}/{total} members also seen at "
            f"≥{CHURN_PROVIDER_THRESHOLD} other providers within {CHURN_WINDOW_DAYS} days"
        ]
        return (
            round(score, 4),
            flags,
            {"churned_member_count": churned, "churn_rate": round(rate, 4)},
        )

    # ── Signal 5: Zero-stay inpatient billing — FIX 4 ────────────────────────

    def _signal_zero_stay_inpatient(
        self, claim: Claim, features: Optional[ClaimFeature]
    ) -> Tuple[float, List[str], Dict]:
        # FIX 4: is_inpatient_code() catches WARD-GEN-DAY, SURG02, ICU, etc.
        inpatient = [
            (s.service_code or "").upper()
            for s in (claim.services or [])
            if is_inpatient_code((s.service_code or "").upper())
        ]
        los = float(features.length_of_stay or 0) if features else 0.0

        if not inpatient or los > 0:
            return 0.0, [], {"inpatient_codes": inpatient, "length_of_stay": los}

        score = 80.0
        flags = [
            f"Zero-stay inpatient billing: {inpatient} billed with length_of_stay=0 "
            f"— patient never admitted"
        ]
        return (
            round(score, 4),
            flags,
            {"inpatient_codes": inpatient, "length_of_stay": los},
        )

    # ── Signal 6: Amount uniformity ───────────────────────────────────────────

    def _signal_amount_uniformity(self, metrics: Dict) -> Tuple[float, List[str], Dict]:
        cv = metrics["amount_cv"]
        mean = metrics["amount_mean"]
        total = metrics["total_recent_claims"]
        if total < 5:
            return 0.0, [], {"reason": "Too few claims to assess uniformity"}
        if cv <= AMOUNT_CV_THRESHOLD:
            score = 75.0
            flags = [
                f"Suspiciously uniform billing — CV {cv:.3f} "
                f"(mean KES {mean:,.0f}, std KES {metrics['amount_std']:,.0f}). Copy-paste fraud."
            ]
            return round(score, 4), flags, {"amount_cv": cv, "amount_mean": mean}
        return 0.0, [], {"amount_cv": cv}

    # ── Signal 7: New provider instant volume ─────────────────────────────────

    def _signal_new_provider_volume(
        self, provider: Provider, metrics: Dict
    ) -> Tuple[float, List[str], Dict]:
        registration_date = getattr(provider, "registration_date", None) or getattr(
            provider, "created_at", None
        )
        if not registration_date:
            return 0.0, [], {"reason": "No registration date available"}
        now = datetime.now(UTC)
        age_days = (now - registration_date).days
        cpd = metrics["claims_per_day"]
        if age_days <= NEW_PROVIDER_DAYS and cpd >= NEW_PROVIDER_DAILY_VOLUME:
            score = 80.0
            flags = [
                f"New provider ({age_days}d old) submitting {cpd:.1f} claims/day — no ramp-up"
            ]
            return (
                round(score, 4),
                flags,
                {"provider_age_days": age_days, "claims_per_day": cpd},
            )
        return 0.0, [], {"provider_age_days": age_days, "claims_per_day": cpd}

    # ── Signal 8: Claim pattern discontinuity ────────────────────────────────

    def _signal_claim_discontinuity(
        self, metrics: Dict
    ) -> Tuple[float, List[str], Dict]:
        monthly = metrics["monthly_volumes"]
        if len(monthly) < 3:
            return 0.0, [], {"reason": "Insufficient monthly history"}
        values = list(monthly.values())
        peak = max(values)
        peak_idx = values.index(peak)
        recent_avg = sum(values[peak_idx:]) / max(len(values[peak_idx:]), 1)
        if peak >= 10 and recent_avg <= 1.0 and peak_idx < len(values) - 2:
            score = 70.0
            flags = [
                f"Hit-and-run: peak {peak} claims/month dropping to ~{recent_avg:.1f}/month"
            ]
            return round(score, 4), flags, {"monthly_volumes": monthly, "peak": peak}
        return 0.0, [], {"monthly_volumes": monthly}
