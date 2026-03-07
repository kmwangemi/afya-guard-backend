"""
SHA Fraud Detection — Upcoding Detector (Multi-Method)

Detects inflated billing using four complementary methods:

  Method 1 — Medical Logic
    ICD-11 → CPT/service code compatibility checks.
    Flags services that are clinically incompatible with the stated diagnosis,
    mutually exclusive billing combinations, and inpatient codes on outpatient claims.

  Method 2 — Statistical Outlier Detection
    Z-score > 3σ against diagnosis-specific cost distributions.
    Z-score > 2σ against peer facility benchmarks (same facility level).
    Both use reference tables from upcoding_medical_db.

  Method 3 — Peer Facility Comparison
    Compares claim cost against the mean/std of similar-level facilities.
    Flags providers billing significantly above their peer group.

  Method 4 — ML Random Forest
    Trained on 15 engineered features to detect subtle multi-signal patterns
    that individual rules would miss. Score is 0–100 probability.

Final score = weighted combination of all four methods:
    medical_logic  × 0.35
    statistical    × 0.25
    peer           × 0.20
    ml             × 0.20
"""

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np

from app.detectors.base_detector import BaseDetector, DetectorResult
from app.detectors.upcoding_medical_db import (
    INPATIENT_ONLY_CODES,
    MIN_FACILITY_LEVEL_FOR_SERVICE,
    MUTUALLY_EXCLUSIVE_PAIRS,
    PEER_BENCHMARKS_BY_LEVEL,
    REFERENCE_PRICES,
    get_diagnosis_cost_range,
    get_expected_services,
    get_incompatible_services,
)
from app.models.claim_feature_model import ClaimFeature
from app.models.claim_model import Claim
from app.utils.provider_utils import parse_facility_level

logger = logging.getLogger(__name__)

# ── Module-level ML model cache ───────────────────────────────────────────────
# Populated by load_upcoding_artifacts() called at app startup

_upcoding_rf_model: Optional[object] = None
_upcoding_feature_list: Optional[list] = None


def load_upcoding_artifacts() -> None:
    """
    Load Random Forest model and feature list into module-level cache.
    Call once from FastAPI lifespan startup alongside load_ml_artifacts().

        from app.detectors.upcoding_detector import load_upcoding_artifacts
        load_upcoding_artifacts()
    """
    global _upcoding_rf_model, _upcoding_feature_list
    try:
        import joblib

        from app.core.config import settings

        model_dir = settings.MODEL_DIR
        rf_path = model_dir / "upcoding_rf.joblib"
        feat_path = model_dir / "upcoding_features.joblib"
        if rf_path.exists():
            _upcoding_rf_model = joblib.load(rf_path)
            _upcoding_feature_list = joblib.load(feat_path)
            logger.info(f"Upcoding RF model loaded: {rf_path}")
        else:
            logger.warning(
                f"Upcoding RF model not found at {rf_path} — ML method disabled"
            )
    except Exception as exc:
        logger.error(f"Failed to load upcoding model: {exc}")


# ── Component weights (must sum to 1.0) ───────────────────────────────────────
METHOD_WEIGHTS = {
    "medical_logic": 0.35,
    "statistical": 0.25,
    "peer": 0.20,
    "ml": 0.20,
}

# Z-score thresholds
ZSCORE_CRITICAL: float = 3.0  # > 3σ → strong outlier
ZSCORE_WARNING: float = 2.0  # > 2σ → moderate outlier

PRICE_OVERRUN_MULTIPLIER: float = 2.0
MAX_SCORE: float = 100.0


class UpcodingDetector(BaseDetector):
    """
    Multi-method upcoding detector combining medical logic, statistical
    outlier detection, peer facility comparison, and ML classification.
    """

    async def detect(
        self,
        claim: Claim,
        features: Optional[ClaimFeature] = None,
    ) -> DetectorResult:
        if not claim.services:
            return DetectorResult(
                detector_name=self.name,
                score=0.0,
                fired=False,
                explanation="No service line items to evaluate for upcoding",
                feature_name="upcoded_service_count",
                feature_value="0",
            )

        # ── Collect claim metadata ─────────────────────────────────────────
        is_outpatient = bool(
            claim.claim_type and "OUTPATIENT" in claim.claim_type.upper()
        )
        length_of_stay = float(features.length_of_stay or 0) if features else 0.0
        total_amount = float(claim.total_claim_amount or 0)
        diagnosis_codes = list(claim.diagnosis_codes or [])
        service_codes = [(s.service_code or "").upper() for s in claim.services]
        provider = getattr(claim, "provider", None)
        facility_level = parse_facility_level(provider)
        all_flags: List[str] = []
        all_metadata: Dict = {}

        # ── Method 1: Medical logic ────────────────────────────────────────
        med_score, med_flags, med_meta = self._run_medical_logic(
            claim,
            service_codes,
            diagnosis_codes,
            is_outpatient,
            length_of_stay,
            facility_level,
        )
        all_flags.extend(med_flags)
        all_metadata["medical_logic"] = med_meta

        # ── Method 2: Statistical outlier detection ────────────────────────
        stat_score, stat_flags, stat_meta = self._run_statistical_detection(
            total_amount,
            diagnosis_codes,
            features,
        )
        all_flags.extend(stat_flags)
        all_metadata["statistical"] = stat_meta

        # ── Method 3: Peer facility comparison ────────────────────────────
        peer_score, peer_flags, peer_meta = self._run_peer_comparison(
            total_amount,
            facility_level,
        )
        all_flags.extend(peer_flags)
        all_metadata["peer_comparison"] = peer_meta

        # ── Method 4: ML Random Forest ─────────────────────────────────────
        ml_score, ml_meta = self._run_ml_detection(
            claim,
            service_codes,
            diagnosis_codes,
            is_outpatient,
            length_of_stay,
            facility_level,
            total_amount,
            features,
        )
        all_metadata["ml"] = ml_meta

        # ── Weighted final score ───────────────────────────────────────────
        final_score = (
            med_score * METHOD_WEIGHTS["medical_logic"]
            + stat_score * METHOD_WEIGHTS["statistical"]
            + peer_score * METHOD_WEIGHTS["peer"]
            + ml_score * METHOD_WEIGHTS["ml"]
        )
        final_score = round(min(final_score, MAX_SCORE), 4)
        fired = final_score > 0

        flagged_services = med_meta.get("flagged_service_codes", [])
        explanation = (
            "Upcoding indicators: " + "; ".join(all_flags)
            if all_flags
            else "No upcoding indicators detected"
        )

        return DetectorResult(
            detector_name=self.name,
            score=final_score,
            fired=fired,
            explanation=explanation,
            feature_name="upcoded_service_count",
            feature_value=str(len(flagged_services)),
            metadata={
                "flagged_service_codes": flagged_services,
                "flag_reasons": all_flags,
                "method_scores": {
                    "medical_logic": round(med_score, 4),
                    "statistical": round(stat_score, 4),
                    "peer": round(peer_score, 4),
                    "ml": round(ml_score, 4),
                },
                "method_weights": METHOD_WEIGHTS,
                **all_metadata,
            },
        )

    async def explain(
        self,
        claim: Claim,
        features: Optional[ClaimFeature] = None,
    ) -> Dict[str, float]:
        if not claim.services:
            return {"upcoded_service_count": 0.0}
        flagged = [s for s in claim.services if getattr(s, "is_upcoded", False)]
        return {
            "upcoded_service_count": float(len(flagged)),
            "total_services": float(len(claim.services)),
            "upcoded_ratio": round(len(flagged) / max(len(claim.services), 1), 4),
        }

    # ── Method 1: Medical logic ───────────────────────────────────────────────

    def _run_medical_logic(
        self,
        claim: Claim,
        service_codes: List[str],
        diagnosis_codes: List[str],
        is_outpatient: bool,
        length_of_stay: float,
        facility_level: int,
    ) -> Tuple[float, List[str], Dict]:
        score: float = 0.0
        flags: List[str] = []
        flagged_services = []

        service_set = set(service_codes)

        # 1a. Price overrun — unit price > 2× reference
        for svc in claim.services:
            code = (svc.service_code or "").upper()
            unit_price = float(svc.unit_price or 0)
            ref_price = REFERENCE_PRICES.get(code)
            if ref_price and unit_price > ref_price * PRICE_OVERRUN_MULTIPLIER:
                pct = ((unit_price - ref_price) / ref_price) * 100
                score += 40.0
                flags.append(
                    f"{code}: KES {unit_price:,.0f} vs ref KES {ref_price:,.0f} ({pct:.0f}% over)"
                )
                flagged_services.append(code)
                svc.is_upcoded = True

        # 1b. Incompatible services for stated diagnoses
        incompatible = get_incompatible_services(diagnosis_codes)
        for code in service_codes:
            if code in incompatible:
                score += 35.0
                flags.append(
                    f"{code} is clinically incompatible with diagnoses {diagnosis_codes}"
                )
                if code not in flagged_services:
                    flagged_services.append(code)

        # 1c. Inpatient-only codes on outpatient claim
        if is_outpatient:
            for code in service_codes:
                if any(ioc in code for ioc in INPATIENT_ONLY_CODES):
                    score += 35.0
                    flags.append(f"{code}: inpatient-only code on outpatient claim")
                    if code not in flagged_services:
                        flagged_services.append(code)

        # 1d. Mutually exclusive code pairs
        for pair in MUTUALLY_EXCLUSIVE_PAIRS:
            if pair.issubset(service_set):
                codes_str = " + ".join(pair)
                score += 30.0
                flags.append(f"Mutually exclusive codes billed together: {codes_str}")

        # 1e. Facility level too low for service
        for code in service_codes:
            min_level = MIN_FACILITY_LEVEL_FOR_SERVICE.get(code)
            if min_level and facility_level < min_level:
                score += 30.0
                flags.append(
                    f"{code} requires Level {min_level}+ facility (provider is Level {facility_level})"
                )
                if code not in flagged_services:
                    flagged_services.append(code)

        # 1f. Excessive quantity
        for svc in claim.services:
            if (svc.quantity or 1) > 10:
                score += 20.0
                flags.append(
                    f"{(svc.service_code or '').upper()}: unusual quantity {svc.quantity}"
                )

        # 1g. High-value service with zero LOS
        for svc in claim.services:
            total = float(svc.total_price or 0)
            if total > 50_000 and length_of_stay == 0:
                score += 25.0
                flags.append(
                    f"{(svc.service_code or '').upper()}: KES {total:,.0f} with 0-day stay"
                )

        # 1h. Expected service ratio — how many services match the diagnosis
        expected = get_expected_services(diagnosis_codes)
        if expected and service_codes:
            matched = sum(1 for c in service_codes if c in expected)
            ratio = matched / len(service_codes)
            if ratio < 0.3:  # less than 30% of services match any diagnosis
                score += 20.0
                flags.append(
                    f"Only {ratio*100:.0f}% of services match stated diagnoses"
                )

        return (
            round(min(score, MAX_SCORE), 4),
            flags,
            {
                "flagged_service_codes": flagged_services,
                "incompatible_services": list(incompatible & set(service_codes)),
            },
        )

    # ── Method 2: Statistical outlier detection ───────────────────────────────

    def _run_statistical_detection(
        self,
        total_amount: float,
        diagnosis_codes: List[str],
        features: Optional[ClaimFeature],
    ) -> Tuple[float, List[str], Dict]:
        score: float = 0.0
        flags: List[str] = []

        # 2a. Diagnosis cost z-score (from ClaimFeature if available)
        diag_zscore = float(features.diagnosis_cost_zscore or 0) if features else 0.0

        if abs(diag_zscore) == 0 and diagnosis_codes:
            # Fall back to reference table z-score
            cost_range = get_diagnosis_cost_range(diagnosis_codes)
            if cost_range:
                _, max_typical, _ = cost_range
                estimated_std = max_typical * 0.4
                if estimated_std > 0:
                    diag_zscore = (total_amount - max_typical) / estimated_std

        if diag_zscore > ZSCORE_CRITICAL:
            score += 60.0
            flags.append(
                f"Diagnosis cost z-score {diag_zscore:.2f}σ — extreme outlier (>{ZSCORE_CRITICAL}σ)"
            )
        elif diag_zscore > ZSCORE_WARNING:
            score += 35.0
            flags.append(
                f"Diagnosis cost z-score {diag_zscore:.2f}σ — above warning threshold (>{ZSCORE_WARNING}σ)"
            )

        # 2b. Absolute cost ceiling check
        cost_range = get_diagnosis_cost_range(diagnosis_codes)
        if cost_range:
            _, _, absolute_max = cost_range
            if total_amount > absolute_max:
                pct_over = ((total_amount - absolute_max) / absolute_max) * 100
                score += 40.0
                flags.append(
                    f"Claim KES {total_amount:,.0f} exceeds absolute max "
                    f"KES {absolute_max:,.0f} for diagnoses ({pct_over:.0f}% over)"
                )

        return (
            round(min(score, MAX_SCORE), 4),
            flags,
            {
                "diagnosis_cost_zscore": round(diag_zscore, 4),
                "cost_range": cost_range,
                "total_amount": total_amount,
            },
        )

    # ── Method 3: Peer facility comparison ───────────────────────────────────

    def _run_peer_comparison(
        self,
        total_amount: float,
        facility_level: int,
    ) -> Tuple[float, List[str], Dict]:
        score: float = 0.0
        flags: List[str] = []

        benchmark = PEER_BENCHMARKS_BY_LEVEL.get(facility_level)
        if not benchmark:
            return 0.0, [], {"reason": "No benchmark for facility level"}

        mean = benchmark["mean"]
        std = benchmark["std"] or 1.0
        peer_zscore = (total_amount - mean) / std

        if peer_zscore > ZSCORE_CRITICAL:
            score = 80.0
            flags.append(
                f"Peer comparison: {peer_zscore:.2f}σ above Level {facility_level} mean "
                f"(KES {total_amount:,.0f} vs peer mean KES {mean:,.0f})"
            )
        elif peer_zscore > ZSCORE_WARNING:
            score = 45.0
            flags.append(
                f"Peer comparison: {peer_zscore:.2f}σ above Level {facility_level} mean "
                f"(KES {total_amount:,.0f} vs peer mean KES {mean:,.0f})"
            )

        return (
            round(min(score, MAX_SCORE), 4),
            flags,
            {
                "peer_zscore": round(peer_zscore, 4),
                "facility_level": facility_level,
                "peer_mean": mean,
                "peer_std": std,
            },
        )

    # ── Method 4: ML Random Forest ────────────────────────────────────────────

    def _run_ml_detection(
        self,
        claim: Claim,
        service_codes: List[str],
        diagnosis_codes: List[str],
        is_outpatient: bool,
        length_of_stay: float,
        facility_level: int,
        total_amount: float,
        features: Optional[ClaimFeature],
    ) -> Tuple[float, Dict]:
        if _upcoding_rf_model is None:
            return 0.0, {"reason": "ML model not loaded"}

        try:
            import pandas as pd

            service_count = len(claim.services)
            expected = get_expected_services(diagnosis_codes)
            incompatible = get_incompatible_services(diagnosis_codes)
            service_set = set(service_codes)

            # Compute per-service signals
            price_overruns = []
            max_qty = 1
            for svc in claim.services:
                code = (svc.service_code or "").upper()
                unit_p = float(svc.unit_price or 0)
                ref_p = REFERENCE_PRICES.get(code)
                if ref_p and ref_p > 0:
                    price_overruns.append(unit_p / ref_p)
                qty = int(svc.quantity or 1)
                if qty > max_qty:
                    max_qty = qty

            max_price_overrun = max(price_overruns) if price_overruns else 1.0

            matched_expected = sum(1 for c in service_codes if c in expected)
            expected_ratio = matched_expected / max(service_count, 1)

            has_incompatible = int(bool(incompatible & service_set))
            has_inpatient_out = int(
                is_outpatient
                and any(
                    any(ioc in c for ioc in INPATIENT_ONLY_CODES) for c in service_codes
                )
            )
            has_mutual_excl = int(
                any(pair.issubset(service_set) for pair in MUTUALLY_EXCLUSIVE_PAIRS)
            )
            has_hi_val_zero_los = int(
                any(float(svc.total_price or 0) > 50_000 for svc in claim.services)
                and length_of_stay == 0
            )
            fac_mismatch = int(
                any(
                    MIN_FACILITY_LEVEL_FOR_SERVICE.get(
                        (svc.service_code or "").upper(), 0
                    )
                    > facility_level
                    for svc in claim.services
                )
            )

            diag_zscore = (
                float(features.diagnosis_cost_zscore or 0) if features else 0.0
            )
            benchmark = PEER_BENCHMARKS_BY_LEVEL.get(
                facility_level, {"mean": 18_000, "std": 7_000}
            )
            peer_zscore = (total_amount - benchmark["mean"]) / max(benchmark["std"], 1)

            row = {
                "price_overrun_ratio": max_price_overrun,
                "incompatible_service_count": len(incompatible & service_set),
                "inpatient_code_outpatient": has_inpatient_out,
                "mutual_exclusion_violation": has_mutual_excl,
                "high_value_zero_los": has_hi_val_zero_los,
                "facility_level_mismatch": fac_mismatch,
                "diagnosis_cost_zscore": diag_zscore,
                "peer_cost_zscore": round(peer_zscore, 4),
                "service_count": service_count,
                "max_quantity": max_qty,
                "amount_per_service": round(total_amount / max(service_count, 1), 2),
                "log_amount": float(np.log1p(total_amount)),
                "claim_type_enc": 1 if is_outpatient else 0,
                "facility_level": facility_level,
                "expected_service_ratio": round(expected_ratio, 4),
            }

            feature_list = _upcoding_feature_list or list(row.keys())
            df = pd.DataFrame([row])[feature_list]
            prob = float(_upcoding_rf_model.predict_proba(df)[0][1]) * 100

            # Feature importances as explanation
            importances = dict(
                zip(
                    feature_list,
                    [
                        round(float(v), 6)
                        for v in _upcoding_rf_model.feature_importances_
                    ],
                )
            )

            return round(prob, 4), {
                "ml_probability": round(prob, 4),
                "feature_values": row,
                "feature_importance": importances,
            }

        except Exception as exc:
            logger.warning(f"Upcoding ML scoring failed: {exc}")
            return 0.0, {"reason": str(exc)}
