from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from datetime import datetime, timedelta
from decimal import Decimal

from app.models import Claim, Provider, Patient, FraudAlert, FraudSeverity


class DuplicateDetector:
    """
    Detect duplicate claims: same service billed multiple times.

    Field changes from old version:
      - patient_national_id  →  patient_sha_number  (SHA form has no National ID)
      - service_date         →  visit_admission_date
      - claim_amount         →  total_claim_amount
    """

    def __init__(self, db: Session):
        self.db = db

    async def analyze_claim(self, claim: Claim) -> Dict[str, Any]:
        """Analyze a claim for duplicate indicators."""
        flags: List[Dict[str, Any]] = []
        risk_score = 0.0

        # Guard: need at minimum the SHA number and admission date to query
        if not claim.patient_sha_number or not claim.visit_admission_date:
            return {
                "module": "duplicate",
                "risk_score": 0.0,
                "flags": [],
                "is_high_risk": False,
                "warning": "Insufficient patient/date data for duplicate check",
            }

        # ── 1. Exact duplicate ────────────────────────────────────────────
        # Same SHA number + same admission date + same total_claim_amount
        exact_duplicates = (
            self.db.query(Claim)
            .filter(
                Claim.id != claim.id,
                Claim.patient_sha_number == claim.patient_sha_number,
                Claim.visit_admission_date == claim.visit_admission_date,
                Claim.total_claim_amount == claim.total_claim_amount,
            )
            .all()
        )

        if exact_duplicates:
            risk_score += 100.0
            flags.append(
                {
                    "type": "exact_duplicate",
                    "severity": FraudSeverity.CRITICAL,
                    "description": (
                        f"Found {len(exact_duplicates)} exact matching claim(s) for same "
                        "SHA member, admission date, and claim amount"
                    ),
                    "score": 100.0,
                    "evidence": {
                        "duplicate_claim_ids": [c.id for c in exact_duplicates]
                    },
                }
            )

        # ── 2. Rolling duplicate ──────────────────────────────────────────
        # Same SHA number + same provider + same total_claim_amount within ±2 days
        if not exact_duplicates:
            rolling_duplicates = (
                self.db.query(Claim)
                .filter(
                    Claim.id != claim.id,
                    Claim.patient_sha_number == claim.patient_sha_number,
                    Claim.provider_id == claim.provider_id,
                    Claim.total_claim_amount == claim.total_claim_amount,
                    Claim.visit_admission_date
                    >= claim.visit_admission_date - timedelta(days=2),
                    Claim.visit_admission_date
                    <= claim.visit_admission_date + timedelta(days=2),
                )
                .all()
            )

            if rolling_duplicates:
                risk_score += 80.0
                flags.append(
                    {
                        "type": "rolling_duplicate",
                        "severity": FraudSeverity.HIGH,
                        "description": (
                            "Near-identical claim found for same SHA member and provider "
                            "within a 2-day window"
                        ),
                        "score": 80.0,
                        "evidence": {
                            "duplicate_claim_ids": [c.id for c in rolling_duplicates]
                        },
                    }
                )

        # ── 3. Same-day multi-facility billing ────────────────────────────
        # Patient billed at two or more different providers on same admission date
        same_day_other_providers = (
            self.db.query(Claim)
            .filter(
                Claim.id != claim.id,
                Claim.patient_sha_number == claim.patient_sha_number,
                Claim.provider_id != claim.provider_id,
                func.date(Claim.visit_admission_date)
                == func.date(claim.visit_admission_date),
            )
            .all()
        )

        if same_day_other_providers:
            risk_score += 60.0
            flags.append(
                {
                    "type": "same_day_multi_facility",
                    "severity": FraudSeverity.HIGH,
                    "description": (
                        f"SHA member billed at {len(same_day_other_providers) + 1} "
                        "different facilities on the same admission date"
                    ),
                    "score": 60.0,
                    "evidence": {
                        "other_claim_ids": [c.id for c in same_day_other_providers],
                        "other_provider_ids": list(
                            {c.provider_id for c in same_day_other_providers}
                        ),
                    },
                }
            )

        # ── 4. Overlapping inpatient stays ────────────────────────────────
        # Two inpatient claims whose admission→discharge windows overlap
        if (
            claim.visit_type
            and claim.visit_type.lower() == "inpatient"
            and claim.discharge_date
        ):
            overlapping = (
                self.db.query(Claim)
                .filter(
                    Claim.id != claim.id,
                    Claim.patient_sha_number == claim.patient_sha_number,
                    Claim.visit_type.ilike("inpatient"),
                    Claim.discharge_date.isnot(None),
                    # Other claim starts before this one ends AND ends after this one starts
                    Claim.visit_admission_date < claim.discharge_date,
                    Claim.discharge_date > claim.visit_admission_date,
                )
                .all()
            )

            if overlapping:
                risk_score += 85.0
                flags.append(
                    {
                        "type": "overlapping_inpatient_stays",
                        "severity": FraudSeverity.CRITICAL,
                        "description": (
                            "SHA member has overlapping inpatient admission periods — "
                            "physically impossible"
                        ),
                        "score": 85.0,
                        "evidence": {
                            "overlapping_claim_ids": [c.id for c in overlapping],
                            "current_admission": claim.visit_admission_date.isoformat(),
                            "current_discharge": claim.discharge_date.isoformat(),
                        },
                    }
                )

        # ── 5. Same preauth number on multiple claims ─────────────────────
        # A preauth number should authorise exactly one episode of care
        preauth_numbers = {
            line.get("preauth_no")
            for line in (claim.benefit_lines or [])
            if line.get("preauth_no")
        }

        if preauth_numbers:
            for preauth_no in preauth_numbers:
                preauth_conflicts = (
                    self.db.query(Claim)
                    .filter(
                        Claim.id != claim.id,
                        # JSON array contains the preauth number
                        Claim.benefit_lines.cast(str).contains(preauth_no),
                    )
                    .all()
                )
                if preauth_conflicts:
                    risk_score += 70.0
                    flags.append(
                        {
                            "type": "reused_preauth_number",
                            "severity": FraudSeverity.HIGH,
                            "description": (
                                f"Preauthorisation number '{preauth_no}' already used "
                                f"in {len(preauth_conflicts)} other claim(s)"
                            ),
                            "score": 70.0,
                            "evidence": {
                                "preauth_no": preauth_no,
                                "conflicting_claim_ids": [
                                    c.id for c in preauth_conflicts
                                ],
                            },
                        }
                    )
                    break  # One flag per claim is enough

        return {
            "module": "duplicate",
            "risk_score": min(risk_score, 100.0),
            "flags": flags,
            "is_high_risk": risk_score >= 60.0,
        }
