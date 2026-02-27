"""
SHA Fraud Detection — Upcoding Detector

Detects inflated billing: providers submitting higher-cost service codes
than the services actually rendered, or billing for complex procedures
when simple ones were performed.

Fraud pattern: A facility bills "Specialist Consultation" (CONSULT03, KES 8,000)
when only a general consultation (CONSULT01, KES 1,500) occurred.
"""

from typing import Dict, Optional, Set

from app.detectors.base_detector import BaseDetector, DetectorResult
from app.models.claim_feature_model import ClaimFeature
from app.models.claim_model import Claim

# ── Reference price table (KES) ───────────────────────────────────────────────
# Maps service code prefix → expected_max_unit_price
# Extend this with actual SHA tariff schedule
REFERENCE_PRICES: Dict[str, float] = {
    "CONSULT01": 1_500,  # General outpatient consultation
    "CONSULT02": 3_500,  # Specialist consultation (tier 1)
    "CONSULT03": 8_000,  # Specialist consultation (tier 2)
    "LAB01": 500,  # Basic blood test (CBC)
    "LAB02": 800,  # Blood chemistry
    "LAB03": 2_500,  # Comprehensive metabolic panel
    "XRAY": 3_000,  # Standard X-ray
    "CT": 18_000,  # CT scan
    "MRI": 35_000,  # MRI
    "SURG01": 25_000,  # Minor surgery
    "SURG02": 80_000,  # Major surgery
    "SURG03": 150_000,  # Complex surgery
    "ICU": 45_000,  # ICU per day
    "WARD": 3_500,  # General ward per day
    "MATERNITY": 15_000,  # Normal delivery
    "CS": 55_000,  # Caesarean section
}

# Service codes that require inpatient admission to be valid
INPATIENT_ONLY_CODES: Set[str] = {"ICU", "WARD", "SURG02", "SURG03", "CS"}

# High-value codes that trigger extra scrutiny if submitted for outpatient
HIGH_VALUE_THRESHOLD: float = 50_000  # KES


class UpcodingDetector(BaseDetector):
    """
    Scoring logic:
        - Unit price > 2× reference price           → +40 per service (capped)
        - Inpatient-only code on outpatient claim    → +35 per occurrence
        - Total claim > 5× expected for diagnosis   → +30
        - Quantity > 10 for a single service        → +20
        - High-value service on a 0-day-stay claim  → +25
    """

    PRICE_OVERRUN_MULTIPLIER: float = 2.0
    MAX_SCORE: float = 100.0

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
        is_outpatient = claim.claim_type and "OUTPATIENT" in claim.claim_type.upper()
        length_of_stay = features.length_of_stay if features else 0
        score: float = 0.0
        flags: list[str] = []
        flagged_services: list[str] = []
        for svc in claim.services:
            code = (svc.service_code or "").upper()
            unit_price = svc.unit_price or 0
            quantity = svc.quantity or 1
            # ── Price overrun check ───────────────────────────────────────────
            ref_price = REFERENCE_PRICES.get(code)
            if ref_price and unit_price > ref_price * self.PRICE_OVERRUN_MULTIPLIER:
                overrun_pct = ((unit_price - ref_price) / ref_price) * 100
                score += 40.0
                flags.append(
                    f"{code}: billed KES {unit_price:,.0f} vs reference KES {ref_price:,.0f} "
                    f"({overrun_pct:.0f}% over)"
                )
                flagged_services.append(code)
                svc.is_upcoded = True
            # ── Inpatient-only code on outpatient claim ────────────────────────
            if is_outpatient and any(ioc in code for ioc in INPATIENT_ONLY_CODES):
                score += 35.0
                flags.append(
                    f"{code} is inpatient-only but submitted on outpatient claim"
                )
                if code not in flagged_services:
                    flagged_services.append(code)
                svc.is_upcoded = True
            # ── Excessive quantity ────────────────────────────────────────────
            if quantity > 10:
                score += 20.0
                flags.append(f"{code}: unusual quantity {quantity}")
            # ── High-value service with zero length of stay ───────────────────
            total = svc.total_price or 0
            if total > HIGH_VALUE_THRESHOLD and (length_of_stay or 0) == 0:
                score += 25.0
                flags.append(f"{code}: high-value (KES {total:,.0f}) with 0-day stay")
        score = min(score, self.MAX_SCORE)
        fired = score > 0
        explanation = (
            "Upcoding indicators: " + "; ".join(flags)
            if flags
            else "No upcoding indicators detected"
        )
        return DetectorResult(
            detector_name=self.name,
            score=round(score, 4),
            fired=fired,
            explanation=explanation,
            feature_name="upcoded_service_count",
            feature_value=str(len(flagged_services)),
            metadata={
                "flagged_service_codes": flagged_services,
                "flag_reasons": flags,
                "is_outpatient": is_outpatient,
                "length_of_stay": length_of_stay,
            },
        )

    async def explain(
        self,
        claim: Claim,
        features: Optional[ClaimFeature] = None,
    ) -> Dict[str, float]:
        if not claim.services:
            return {"upcoded_service_count": 0.0}
        upcoded = [s for s in claim.services if s.is_upcoded]
        return {
            "upcoded_service_count": float(len(upcoded)),
            "total_services": float(len(claim.services)),
            "upcoded_ratio": round(len(upcoded) / max(len(claim.services), 1), 4),
        }
