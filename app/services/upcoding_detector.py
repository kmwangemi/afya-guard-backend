from typing import Any, Dict, List

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.claim_model import Claim
from app.models.enums_model import FraudSeverity


class UpcodingDetector:
    """
    Detect upcoding: billing for more expensive services than performed,
    or claiming high-value accommodation/procedures without supporting diagnosis.

    Field changes from old version:
      - claim.claim_amount         →  claim.total_claim_amount
      - claim.visit_type           →  claim.claim_type
      - claim.admission_diagnosis  →  claim.raw_payload.get('admission_diagnosis')
    """

    # ICD-11 chapter prefixes for maternity / obstetrics (JA00–JA9Z)
    MATERNITY_ICD11_PREFIXES = ("JA",)

    # Procedures that are only valid for inpatient or day-care stays
    INPATIENT_ONLY_PROCEDURES = {
        "surgery",
        "general anaesthesia",
        "mechanical ventilation",
        "intensive care",
        "icu",
        "hdu",
        "dialysis",
        "blood transfusion",
    }

    # Simple diagnoses that should not attract complex procedures
    SIMPLE_DIAGNOSES = {
        "headache",
        "malaria",
        "influenza",
        "flu",
        "cough",
        "fever",
        "common cold",
        "upper respiratory",
        "minor laceration",
        "sprain",
        "mild gastroenteritis",
    }

    # Keywords for expensive procedures
    COMPLEX_PROCEDURES = {
        "surgery",
        "mri",
        "ct scan",
        "computed tomography",
        "intensive care",
        "cardiac catheterisation",
        "endoscopy",
        "dialysis",
        "chemotherapy",
        "radiotherapy",
        "organ transplant",
    }

    def __init__(self, db: AsyncSession):
        self.db = db

    async def analyze_claim(self, claim: Claim) -> Dict[str, Any]:
        """Analyse a claim for upcoding indicators."""
        flags: List[Dict[str, Any]] = []
        risk_score = 0.0

        visit_type = claim.claim_type or ""
        visit_type_lower = visit_type.lower()
        risk_score = 0.0
        flags = []

        # ── 1. High outpatient bill ──────────────────────────────────────
        if claim.total_claim_amount and visit_type_lower == "outpatient":
            amount = float(claim.total_claim_amount)
            if amount > 50000:
                risk_score += 40.0
                flags.append(
                    {
                        "type": "high_outpatient_bill",
                        "severity": FraudSeverity.HIGH,
                        "description": (
                            f"Outpatient claim for {amount:,.2f} is exceptionally high "
                            "(threshold: 50,000)"
                        ),
                        "score": 40.0,
                        "evidence": {
                            "amount": amount,
                            "visit_type": visit_type,
                        },
                    }
                )
            elif amount > 15_000:
                risk_score += 40.0
                flags.append(
                    {
                        "type": "high_cost_outpatient",
                        "severity": FraudSeverity.MEDIUM,
                        "description": (
                            f"Outpatient claim of KSh {amount:,.2f} typically indicates "
                            "inpatient-level care (threshold: >15,000)"
                        ),
                        "score": 40.0,
                        "evidence": {
                            "total_claim_amount": amount,
                            "visit_type": claim.visit_type,
                        },
                    }
                )

        # ── 2. Diagnostic upcoding (complex/rare code with common signs) ──
        raw = claim.raw_payload or {}
        admission_diag_lower = (raw.get("admission_diagnosis") or "").lower()
        discharge_diag_lower = (raw.get("discharge_diagnosis") or "").lower()
        combined_diagnosis = f"{admission_diag_lower} {discharge_diag_lower}"

        is_simple_diagnosis = any(
            d in combined_diagnosis for d in self.SIMPLE_DIAGNOSES
        )

        # Check for complex procedures with minimal diagnosis (logic)
        complex_keywords = {"surgery", "major", "intensive", "transplant", "cardiac"}
        procedure_descriptions: List[str] = []
        benefit_lines = raw.get("benefit_lines") or []
        for line in benefit_lines:
            desc = (line.get("service_description") or "").lower()
            if desc:
                procedure_descriptions.append(desc)

        related_proc = raw.get("related_procedure")
        if related_proc:
            procedure_descriptions.append(related_proc.lower())

        has_complex_procedure = any(
            cp in desc
            for desc in procedure_descriptions
            for cp in self.COMPLEX_PROCEDURES
        )

        if is_simple_diagnosis and has_complex_procedure:
            risk_score += 75.0
            matched_simple = next(
                (d for d in self.SIMPLE_DIAGNOSES if d in combined_diagnosis), ""
            )
            matched_complex = next(
                (
                    cp
                    for desc in procedure_descriptions
                    for cp in self.COMPLEX_PROCEDURES
                    if cp in desc
                ),
                "",
            )
            flags.append(
                {
                    "type": "diagnosis_procedure_mismatch",
                    "severity": FraudSeverity.HIGH,
                    "description": (
                        "Claim lists complex procedures but provides only vague "
                        "diagnosis information"
                    ),
                    "score": 30.0,
                    "evidence": {
                        "admission_diagnosis": raw.get("admission_diagnosis"),
                        "discharge_diagnosis": raw.get("discharge_diagnosis"),
                        "procedures": procedure_descriptions[:5],
                    },
                }
            )

        # 3. Inpatient setting for outpatient procedures ──────────────
        # (Already partially covered by module 1 but more specific here)
        accom_type = raw.get("accommodation_type")
        if accom_type:
            accom_lower = accom_type.lower()
            if visit_type_lower == "outpatient" and any(
                kw in accom_lower for kw in ["ward", "icu", "hdu", "bed", "theatre"]
            ):
                risk_score += 40.0
                flags.append(
                    {
                        "type": "setting_mismatch",
                        "severity": FraudSeverity.HIGH,
                        "description": (
                            f"Accommodation type '{accom_type}' is only "
                            f"valid for inpatient stays, but visit type is '{visit_type}'"
                        ),
                        "score": 40.0,
                        "evidence": {
                            "accommodation_type": accom_type,
                            "visit_type": visit_type,
                        },
                    }
                )

        # 4. Unbundled services ─────────────────────────────────────────
        # Each benefit line's claim_amount should not exceed its bill_amount
        for i, line in enumerate(benefit_lines):
            bill = line.get("bill_amount")
            clm = line.get("claim_amount")
            if bill is not None and clm is not None and float(clm) > float(bill):
                overage = float(clm) - float(bill)
                risk_score += 55.0
                flags.append(
                    {
                        "type": "claim_exceeds_bill_on_benefit_line",
                        "severity": FraudSeverity.HIGH,
                        "description": (
                            f"Benefit line {i + 1}: claim amount "
                            f"(KSh {float(clm):,.2f}) exceeds billed amount "
                            f"(KSh {float(bill):,.2f}) by KSh {overage:,.2f}"
                        ),
                        "score": 55.0,
                        "evidence": {
                            "line_index": i + 1,
                            "bill_amount": float(bill),
                            "claim_amount": float(clm),
                            "overage": round(overage, 2),
                            "description": line.get("description"),
                        },
                    }
                )

        return {
            "module": "upcoding",
            "risk_score": min(risk_score, 100.0),
            "flags": flags,
            "is_high_risk": risk_score >= 60.0,
        }
