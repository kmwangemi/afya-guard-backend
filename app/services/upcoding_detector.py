# ===========================================================================
# upcoding_detector.py
# ===========================================================================


class UpcodingDetector:
    """
    Detect upcoding: billing for more expensive services than performed,
    or claiming high-value accommodation/procedures without supporting diagnosis.

    Field changes from old version:
      - claim.claim_amount         →  claim.total_claim_amount
      - claim.discharge_diagnosis_codes (list of dicts) → claim.icd11_code (str) +
        individual benefit_lines[n]['icd11_procedure_code']
      - claim.procedures (list)    →  claim.benefit_lines + claim.related_procedure
      - claim.admission_diagnosis  →  same name, unchanged
      - visit_type comparison uses .lower() to handle Title-cased values from extractor
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

    def __init__(self, db: Session):
        self.db = db

    async def analyze_claim(self, claim: Claim) -> Dict[str, Any]:
        """Analyse a claim for upcoding indicators."""
        flags: List[Dict[str, Any]] = []
        risk_score = 0.0

        visit_type_lower = (claim.visit_type or "").lower()

        # ── 1. High-value outpatient claim amount ─────────────────────────
        # Outpatient visits costing > KSh 15,000 without ICU-level accommodation
        # warrant scrutiny; > KSh 50,000 is a strong signal
        if claim.total_claim_amount and visit_type_lower == "outpatient":
            amount = float(claim.total_claim_amount)
            if amount > 50_000:
                risk_score += 65.0
                flags.append(
                    {
                        "type": "very_high_cost_outpatient",
                        "severity": FraudSeverity.HIGH,
                        "description": (
                            f"Outpatient claim of KSh {amount:,.2f} is unusually high "
                            "(threshold: >50,000 for outpatient)"
                        ),
                        "score": 65.0,
                        "evidence": {
                            "total_claim_amount": amount,
                            "visit_type": claim.visit_type,
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

        # ── 2. Diagnosis ↔ procedure mismatch ────────────────────────────
        # Simple diagnosis paired with a complex/expensive procedure
        admission_diag_lower = (claim.admission_diagnosis or "").lower()
        discharge_diag_lower = (claim.discharge_diagnosis or "").lower()
        combined_diagnosis = f"{admission_diag_lower} {discharge_diag_lower}"

        is_simple_diagnosis = any(
            d in combined_diagnosis for d in self.SIMPLE_DIAGNOSES
        )

        # Collect procedure descriptions from benefit lines
        procedure_descriptions: List[str] = []
        for line in claim.benefit_lines or []:
            desc = (line.get("description") or "").lower()
            if desc:
                procedure_descriptions.append(desc)
        if claim.related_procedure:
            procedure_descriptions.append(claim.related_procedure.lower())

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
                        f"Complex procedure ('{matched_complex}') billed for a "
                        f"simple diagnosis ('{matched_simple}')"
                    ),
                    "score": 75.0,
                    "evidence": {
                        "admission_diagnosis": claim.admission_diagnosis,
                        "discharge_diagnosis": claim.discharge_diagnosis,
                        "matched_complex_procedure": matched_complex,
                    },
                }
            )

        # ── 3. Inpatient-only procedures on outpatient claim ──────────────
        if visit_type_lower == "outpatient":
            for desc in procedure_descriptions:
                matched = next(
                    (p for p in self.INPATIENT_ONLY_PROCEDURES if p in desc), None
                )
                if matched:
                    risk_score += 70.0
                    flags.append(
                        {
                            "type": "inpatient_procedure_on_outpatient_claim",
                            "severity": FraudSeverity.HIGH,
                            "description": (
                                f"Procedure '{matched}' is only valid for inpatient or "
                                "day-care admissions but claim is outpatient"
                            ),
                            "score": 70.0,
                            "evidence": {
                                "visit_type": claim.visit_type,
                                "flagged_procedure": matched,
                            },
                        }
                    )
                    break  # One flag per claim is sufficient

        # ── 4. Accommodation type vs visit type mismatch ──────────────────
        # E.g., claiming ICU accommodation on an outpatient visit
        if claim.accommodation_type:
            accom_lower = claim.accommodation_type.lower()
            is_icu_level = accom_lower in {"icu", "hdu", "nicu", "burns"}
            if is_icu_level and visit_type_lower != "inpatient":
                risk_score += 80.0
                flags.append(
                    {
                        "type": "icu_accommodation_non_inpatient",
                        "severity": FraudSeverity.CRITICAL,
                        "description": (
                            f"Accommodation type '{claim.accommodation_type}' is only "
                            f"valid for inpatient stays, but visit type is '{claim.visit_type}'"
                        ),
                        "score": 80.0,
                        "evidence": {
                            "accommodation_type": claim.accommodation_type,
                            "visit_type": claim.visit_type,
                        },
                    }
                )

        # ── 5. Claim amount vs bill amount ratio per benefit line ─────────
        # Each benefit line's claim_amount should not exceed its bill_amount
        for i, line in enumerate(claim.benefit_lines or []):
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
