"""
SHA Fraud Detection — Upcoding Detector (Multi-Method) — PATCHED

Changes vs original:
  [FIX 1] All service codes normalised via normalise_service_code() before
          any reference table lookups. CONSULT-SPEC-001 → CONSULT02 etc.
  [FIX 2] Added quantity × unit_price consistency check in _run_medical_logic().
          Catches total_price manipulation (e.g. ICU: 15×45,000 ≠ 67,500).
  [FIX 3] Expected service ratio now compares normalised claim codes against
          expected canonical codes — ratio was always 0% before because
          "CONSULT-SPEC-001" never matched "CONSULT01" in expected set.
  [FIX 4] Mutually exclusive pair check now uses normalised codes, so
          frozenset({"ICU","WARD"}) correctly fires when claim has both
          "ICU" and "WARD-GEN-DAY".
"""

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np

from app.detectors.base_detector import BaseDetector, DetectorResult
from app.detectors.upcoding_medical_db import (
    MIN_FACILITY_LEVEL_FOR_SERVICE,
    MUTUALLY_EXCLUSIVE_PAIRS,
    PEER_BENCHMARKS_BY_LEVEL,
    REFERENCE_PRICES,
    get_diagnosis_cost_range,
    get_expected_services,
    get_incompatible_services,
    is_inpatient_code,  # FIX 4
    normalise_service_code,  # FIX 1
)
from app.models.claim_feature_model import ClaimFeature
from app.models.claim_model import Claim
from app.utils.provider_utils import parse_facility_level

logger = logging.getLogger(__name__)

_upcoding_rf_model: Optional[object] = None
_upcoding_feature_list: Optional[list] = None


def load_upcoding_artifacts() -> None:
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


METHOD_WEIGHTS = {
    "medical_logic": 0.35,
    "statistical": 0.25,
    "peer": 0.20,
    "ml": 0.20,
}

ZSCORE_CRITICAL: float = 3.0
ZSCORE_WARNING: float = 2.0
PRICE_OVERRUN_MULTIPLIER: float = 2.0
MAX_SCORE: float = 100.0


class UpcodingDetector(BaseDetector):

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

        is_outpatient = bool(
            claim.claim_type and "OUTPATIENT" in claim.claim_type.upper()
        )
        length_of_stay = float(features.length_of_stay or 0) if features else 0.0
        total_amount = float(claim.total_claim_amount or 0)
        diagnosis_codes = list(claim.diagnosis_codes or [])
        provider = getattr(claim, "provider", None)
        facility_level = parse_facility_level(provider)

        # FIX 1: Normalise ALL service codes once at the top.
        # raw_to_canonical maps original code → canonical key (or None for drugs).
        # canonical_codes is the normalised list used for all set/map lookups.
        raw_to_canonical = {
            (s.service_code or "").upper(): normalise_service_code(
                (s.service_code or "").upper()
            )
            for s in claim.services
        }
        canonical_codes = [
            raw_to_canonical[s] for s in raw_to_canonical if raw_to_canonical[s]
        ]
        canonical_set = set(canonical_codes)

        all_flags: List[str] = []
        all_metadata: Dict = {}

        med_score, med_flags, med_meta = self._run_medical_logic(
            claim,
            raw_to_canonical,
            canonical_codes,
            canonical_set,
            diagnosis_codes,
            is_outpatient,
            length_of_stay,
            facility_level,
        )
        all_flags.extend(med_flags)
        all_metadata["medical_logic"] = med_meta

        stat_score, stat_flags, stat_meta = self._run_statistical_detection(
            total_amount,
            diagnosis_codes,
            features,
        )
        all_flags.extend(stat_flags)
        all_metadata["statistical"] = stat_meta

        peer_score, peer_flags, peer_meta = self._run_peer_comparison(
            total_amount,
            facility_level,
        )
        all_flags.extend(peer_flags)
        all_metadata["peer_comparison"] = peer_meta

        ml_score, ml_meta = self._run_ml_detection(
            claim,
            canonical_codes,
            diagnosis_codes,
            is_outpatient,
            length_of_stay,
            facility_level,
            total_amount,
            features,
        )
        all_metadata["ml"] = ml_meta

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

    # ── Method 1: Medical Logic ───────────────────────────────────────────────

    def _run_medical_logic(
        self,
        claim: Claim,
        raw_to_canonical: Dict[str, Optional[str]],
        canonical_codes: List[str],
        canonical_set: set,
        diagnosis_codes: List[str],
        is_outpatient: bool,
        length_of_stay: float,
        facility_level: int,
    ) -> Tuple[float, List[str], Dict]:
        score: float = 0.0
        flags: List[str] = []
        flagged_services = []

        # 1a. Price overrun — uses normalised canonical code
        for svc in claim.services:
            raw = (svc.service_code or "").upper()
            canonical = raw_to_canonical.get(raw)
            unit_price = float(svc.unit_price or 0)
            ref_price = REFERENCE_PRICES.get(canonical) if canonical else None
            if ref_price and unit_price > ref_price * PRICE_OVERRUN_MULTIPLIER:
                pct = ((unit_price - ref_price) / ref_price) * 100
                score += 40.0
                flags.append(
                    f"{raw} (→{canonical}): KES {unit_price:,.0f} vs ref "
                    f"KES {ref_price:,.0f} ({pct:.0f}% over)"
                )
                flagged_services.append(raw)
                svc.is_upcoded = True

        # FIX 2: Quantity × unit_price consistency check
        for svc in claim.services:
            unit = float(svc.unit_price or 0)
            qty = int(svc.quantity or 1)
            total = float(svc.total_price or 0)
            expected_total = unit * qty
            if expected_total > 0 and total > 0:
                diff_pct = abs(expected_total - total) / expected_total
                if diff_pct > 0.01:  # >1% tolerance
                    score += 25.0
                    raw = (svc.service_code or "").upper()
                    flags.append(
                        f"{raw}: total_price KES {total:,.0f} ≠ "
                        f"unit_price KES {unit:,.0f} × qty {qty} = KES {expected_total:,.0f} "
                        f"({diff_pct*100:.0f}% discrepancy — possible manipulation)"
                    )
                    if raw not in flagged_services:
                        flagged_services.append(raw)

        # 1b. Incompatible services — match against canonical codes
        incompatible = get_incompatible_services(diagnosis_codes)
        for canonical in canonical_codes:
            if canonical in incompatible:
                score += 35.0
                flags.append(
                    f"{canonical} is clinically incompatible with diagnoses {diagnosis_codes}"
                )
                if canonical not in flagged_services:
                    flagged_services.append(canonical)

        # 1c. Inpatient-only codes on outpatient — FIX 4: use is_inpatient_code()
        if is_outpatient:
            for svc in claim.services:
                raw = (svc.service_code or "").upper()
                canonical = raw_to_canonical.get(raw) or raw
                if is_inpatient_code(canonical):
                    score += 35.0
                    flags.append(f"{raw}: inpatient-only code on outpatient claim")
                    if raw not in flagged_services:
                        flagged_services.append(raw)

        # 1d. Mutually exclusive pairs — FIX 4: check against normalised canonical_set
        for pair in MUTUALLY_EXCLUSIVE_PAIRS:
            if pair.issubset(canonical_set):
                codes_str = " + ".join(pair)
                score += 30.0
                flags.append(f"Mutually exclusive codes billed together: {codes_str}")

        # 1e. Facility level
        for canonical in canonical_codes:
            min_level = MIN_FACILITY_LEVEL_FOR_SERVICE.get(canonical)
            if min_level and facility_level < min_level:
                score += 30.0
                flags.append(
                    f"{canonical} requires Level {min_level}+ "
                    f"(provider is Level {facility_level})"
                )
                if canonical not in flagged_services:
                    flagged_services.append(canonical)

        # 1f. Excessive quantity
        for svc in claim.services:
            if (svc.quantity or 1) > 10:
                score += 20.0
                flags.append(
                    f"{(svc.service_code or '').upper()}: unusual quantity {svc.quantity}"
                )

        # 1g. High-value service with zero LOS
        for svc in claim.services:
            # FIX 2: use computed total (unit × qty) not submitted total_price
            unit = float(svc.unit_price or 0)
            qty = int(svc.quantity or 1)
            computed_total = unit * qty
            if computed_total > 50_000 and length_of_stay == 0:
                score += 25.0
                flags.append(
                    f"{(svc.service_code or '').upper()}: "
                    f"KES {computed_total:,.0f} (computed) with 0-day stay"
                )

        # FIX 3: Expected service ratio — compare NORMALISED codes to expected canonical set
        expected = get_expected_services(diagnosis_codes)
        if expected and canonical_codes:
            matched = sum(1 for c in canonical_codes if c in expected)
            ratio = matched / len(canonical_codes)
            if ratio < 0.3:
                score += 20.0
                flags.append(
                    f"Only {ratio*100:.0f}% of services (normalised) match stated diagnoses "
                    f"(matched {matched}/{len(canonical_codes)})"
                )

        return (
            round(min(score, MAX_SCORE), 4),
            flags,
            {
                "flagged_service_codes": flagged_services,
                "incompatible_services": list(incompatible & canonical_set),
                "canonical_code_map": raw_to_canonical,
            },
        )

    # ── Method 2: Statistical outlier ────────────────────────────────────────

    def _run_statistical_detection(
        self,
        total_amount: float,
        diagnosis_codes: List[str],
        features: Optional[ClaimFeature],
    ) -> Tuple[float, List[str], Dict]:
        score: float = 0.0
        flags: List[str] = []

        diag_zscore = float(features.diagnosis_cost_zscore or 0) if features else 0.0

        if abs(diag_zscore) == 0 and diagnosis_codes:
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
            {"diagnosis_cost_zscore": round(diag_zscore, 4), "cost_range": cost_range},
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
                f"Peer comparison: {peer_zscore:.2f}σ above Level {facility_level} mean"
            )

        return (
            round(min(score, MAX_SCORE), 4),
            flags,
            {"peer_zscore": round(peer_zscore, 4), "peer_mean": mean, "peer_std": std},
        )

    # ── Method 4: ML Random Forest ────────────────────────────────────────────

    def _run_ml_detection(
        self,
        claim: Claim,
        canonical_codes: List[str],
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
            canonical_set = set(canonical_codes)

            price_overruns = []
            max_qty = 1
            for svc in claim.services:
                raw = (svc.service_code or "").upper()
                can = normalise_service_code(raw)
                unit_p = float(svc.unit_price or 0)
                ref_p = REFERENCE_PRICES.get(can) if can else None
                if ref_p and ref_p > 0:
                    price_overruns.append(unit_p / ref_p)
                qty = int(svc.quantity or 1)
                if qty > max_qty:
                    max_qty = qty

            max_price_overrun = max(price_overruns) if price_overruns else 1.0
            matched_expected = sum(1 for c in canonical_codes if c in expected)
            expected_ratio = matched_expected / max(service_count, 1)
            has_incompatible = int(bool(incompatible & canonical_set))
            has_inpatient_out = int(
                is_outpatient and any(is_inpatient_code(c) for c in canonical_codes)
            )
            has_mutual_excl = int(
                any(pair.issubset(canonical_set) for pair in MUTUALLY_EXCLUSIVE_PAIRS)
            )
            has_hi_val_zero_los = int(
                any(
                    float(svc.unit_price or 0) * int(svc.quantity or 1) > 50_000
                    for svc in claim.services
                )
                and length_of_stay == 0
            )
            fac_mismatch = int(
                any(
                    MIN_FACILITY_LEVEL_FOR_SERVICE.get(
                        normalise_service_code((svc.service_code or "").upper()) or "",
                        0,
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
                "incompatible_service_count": len(incompatible & canonical_set),
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
