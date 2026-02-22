from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.claim_model import Claim
from app.models.enums_model import FraudSeverity
from app.models.provider_model import Provider


class PhantomPatientDetector:
    """
    Identify claims for non-existent, deceased, or identity-mismatched patients.

    Field changes from old version:
      - patient_national_id → patient_sha_number (primary patient identifier)
      - service_date        → visit_admission_date
      - discharge_diagnosis_codes (list of dicts) → icd11_code (str from extractor)
      - procedures (list)   → benefit_lines + related_procedure
      - is_referred         → was_referred (bool)
    """

    def __init__(self, db: Session):
        self.db = db
        self.sha_registry_url = getattr(
            __import__("app.config", fromlist=["settings"]).settings,
            "SHA_REGISTRY_API_URL",
            None,
        )
        self.sha_registry_key = getattr(
            __import__("app.config", fromlist=["settings"]).settings,
            "SHA_REGISTRY_API_KEY",
            None,
        )

    async def analyze_claim(self, claim: Claim) -> Dict[str, Any]:
        """Analyse claim for phantom patient indicators."""
        flags: List[Dict[str, Any]] = []
        risk_score = 0.0

        if not claim.patient_sha_number:
            return {
                "module": "phantom_patient",
                "risk_score": 50.0,
                "flags": [
                    {
                        "type": "missing_sha_number",
                        "severity": FraudSeverity.HIGH,
                        "description": "Claim has no SHA member number — cannot verify patient",
                        "score": 50.0,
                    }
                ],
                "is_high_risk": True,
            }

        # ── 1. SHA registry verification (40 points) ─────────────────────
        registry_result = await self._verify_with_sha_registry(claim.patient_sha_number)

        if not registry_result["exists"]:
            risk_score += 40.0
            flags.append(
                {
                    "type": "sha_number_not_found",
                    "severity": FraudSeverity.CRITICAL,
                    "description": "SHA member number not found in the SHA registry",
                    "score": 40.0,
                    "evidence": {"sha_number": claim.patient_sha_number},
                }
            )

        if registry_result.get("is_deceased"):
            risk_score += 40.0
            flags.append(
                {
                    "type": "deceased_member",
                    "severity": FraudSeverity.CRITICAL,
                    "description": (
                        f"SHA member recorded as deceased on "
                        f"{registry_result.get('death_date', 'unknown date')}"
                    ),
                    "score": 40.0,
                    "evidence": {
                        "sha_number": claim.patient_sha_number,
                        "death_date": registry_result.get("death_date"),
                    },
                }
            )

        # ── 2. Geographic impossibility (30 points) ───────────────────────
        geo_check = await self._check_geographic_impossibility(claim)
        if geo_check["is_impossible"]:
            risk_score += 30.0
            flags.append(
                {
                    "type": "geographic_impossibility",
                    "severity": FraudSeverity.HIGH,
                    "description": geo_check["description"],
                    "score": 30.0,
                    "evidence": geo_check["evidence"],
                }
            )

        # ── 3. Medical plausibility (up to 35 points) ─────────────────────
        plausibility = self._check_medical_plausibility(claim, registry_result)
        if plausibility:
            risk_score += plausibility["score"]
            flags.append(plausibility)

        # ── 4. Excessive visit frequency (up to 20 points) ────────────────
        freq = await self._analyze_visit_frequency(claim.patient_sha_number)
        if freq["is_excessive"]:
            risk_score += freq["score"]
            flags.append(
                {
                    "type": "excessive_visits",
                    "severity": FraudSeverity.MEDIUM,
                    "description": freq["description"],
                    "score": freq["score"],
                    "evidence": {
                        "visit_count": freq["count"],
                        "period_days": 30,
                    },
                }
            )

        # ── 5. Name mismatch against registry (15 points) ─────────────────
        # The extractor captures patient_last_name and patient_first_name separately
        if registry_result.get("exists") and not registry_result.get("api_error"):
            name_ok = self._check_name_match(claim, registry_result)
            if not name_ok:
                risk_score += 15.0
                flags.append(
                    {
                        "type": "name_mismatch_vs_registry",
                        "severity": FraudSeverity.MEDIUM,
                        "description": (
                            "Patient name on claim does not match SHA registry record"
                        ),
                        "score": 15.0,
                        "evidence": {
                            "claim_last_name": claim.patient_last_name,
                            "claim_first_name": claim.patient_first_name,
                            "registry_name": registry_result.get("full_name"),
                        },
                    }
                )

        return {
            "module": "phantom_patient",
            "risk_score": min(100.0, risk_score),
            "flags": flags,
            "is_high_risk": risk_score >= 70.0,
        }

    # ── Helpers ──────────────────────────────────────────────────────────

    async def _verify_with_sha_registry(self, sha_number: str) -> Dict[str, Any]:
        """
        Verify SHA member via the SHA member registry API.
        Returns a safe default (exists=True) on API error so claims are
        not blocked by infrastructure failures.
        """
        import httpx

        if not self.sha_registry_url:
            return {
                "exists": True,
                "api_error": True,
                "error_message": "Registry URL not configured",
            }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.sha_registry_url}/verify-member",
                    headers={
                        "Authorization": f"Bearer {self.sha_registry_key}",
                        "Content-Type": "application/json",
                    },
                    json={"sha_number": sha_number},
                )
                if response.status_code == 200:
                    data = response.json()
                    return {
                        "exists": data.get("exists", False),
                        "is_deceased": data.get("is_deceased", False),
                        "death_date": data.get("death_date"),
                        "gender": data.get("gender"),
                        "date_of_birth": data.get("date_of_birth"),
                        "county": data.get("county"),
                        "full_name": data.get("full_name"),
                        "api_error": False,
                    }
                return {
                    "exists": True,
                    "is_deceased": False,
                    "api_error": True,
                    "error_code": response.status_code,
                }
        except Exception as exc:
            return {
                "exists": True,
                "is_deceased": False,
                "api_error": True,
                "error_message": str(exc),
            }

    async def _check_geographic_impossibility(self, claim: Claim) -> Dict[str, Any]:
        """
        Flag if the SHA member has another claim at a different county
        on the same admission date — physically impossible.
        """
        if not claim.visit_admission_date:
            return {"is_impossible": False}

        current_provider = (
            claim.provider
            or self.db.query(Provider).filter(Provider.id == claim.provider_id).first()
        )
        if not current_provider or not getattr(current_provider, "county", None):
            return {"is_impossible": False}

        same_day_claims = (
            self.db.query(Claim)
            .join(Provider, Claim.provider_id == Provider.id)
            .filter(
                Claim.patient_sha_number == claim.patient_sha_number,
                Claim.provider_id != claim.provider_id,
                func.date(Claim.visit_admission_date)
                == func.date(claim.visit_admission_date),
            )
            .all()
        )

        for other in same_day_claims:
            if other.provider and other.provider.county != current_provider.county:
                hours_apart = abs(
                    (
                        other.visit_admission_date - claim.visit_admission_date
                    ).total_seconds()
                    / 3600
                )
                return {
                    "is_impossible": True,
                    "description": (
                        f"SHA member claimed services in {current_provider.county} "
                        f"and {other.provider.county} on the same day "
                        f"({hours_apart:.1f} hours apart)"
                    ),
                    "evidence": {
                        "claim_1": {
                            "provider": current_provider.name,
                            "county": current_provider.county,
                            "admission": claim.visit_admission_date.isoformat(),
                        },
                        "claim_2": {
                            "claim_number": other.claim_number,
                            "provider": other.provider.name,
                            "county": other.provider.county,
                            "admission": other.visit_admission_date.isoformat(),
                        },
                        "hours_apart": round(hours_apart, 1),
                    },
                }

        return {"is_impossible": False}

    def _check_medical_plausibility(
        self, claim: Claim, registry_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Flag medically impossible combinations:
          - Maternity/pregnancy ICD-11 codes for a male member
          - Delivery procedures for elderly or male patients
          - Paediatric codes for adults

        Uses claim.icd11_code (single str from extractor) and
        claim.benefit_lines[n]['icd11_procedure_code'] for line-level codes.
        """
        gender = (registry_data.get("gender") or "").upper()
        dob = registry_data.get("date_of_birth")
        age: Optional[int] = None

        if dob:
            try:
                dob_dt = datetime.fromisoformat(dob) if isinstance(dob, str) else dob
                age = (datetime.now() - dob_dt).days // 365
            except Exception:
                pass

        # Collect all ICD-11 codes: top-level + per benefit-line
        all_icd_codes: List[str] = []
        if claim.icd11_code:
            all_icd_codes.append(claim.icd11_code)
        for line in claim.benefit_lines or []:
            code = line.get("icd11_procedure_code") or ""
            if code:
                all_icd_codes.append(code)

        # Maternity ICD-11 codes (JA prefix) for male patients
        for code in all_icd_codes:
            if code.startswith("JA") and gender == "M":
                return {
                    "type": "impossible_diagnosis",
                    "severity": FraudSeverity.CRITICAL,
                    "description": (
                        f"Maternity/obstetrics ICD-11 code ({code}) on claim "
                        "for a male SHA member"
                    ),
                    "score": 35.0,
                    "evidence": {"icd11_code": code, "gender": gender},
                }

        # Delivery procedures for male or elderly (>65) patients
        delivery_codes = {
            "59400",
            "59409",
            "59410",
            "59510",
            "59514",
            "59515",
            "59610",
            "59612",
            "59614",
        }
        for line in claim.benefit_lines or []:
            proc_code = (line.get("icd11_procedure_code") or "").strip()
            if proc_code in delivery_codes:
                if gender == "M":
                    return {
                        "type": "impossible_procedure",
                        "severity": FraudSeverity.CRITICAL,
                        "description": (
                            f"Delivery procedure ({proc_code}) for a male SHA member"
                        ),
                        "score": 35.0,
                        "evidence": {"procedure_code": proc_code, "gender": gender},
                    }
                if age is not None and age > 65:
                    return {
                        "type": "unlikely_procedure",
                        "severity": FraudSeverity.HIGH,
                        "description": (
                            f"Delivery procedure ({proc_code}) for a "
                            f"{age}-year-old patient"
                        ),
                        "score": 25.0,
                        "evidence": {"procedure_code": proc_code, "age": age},
                    }

        # Paediatric-specific ICD-11 codes (LA prefix) for adults
        for code in all_icd_codes:
            if code.startswith("LA") and age is not None and age > 18:
                return {
                    "type": "age_mismatch",
                    "severity": FraudSeverity.MEDIUM,
                    "description": (
                        f"Paediatric ICD-11 code ({code}) on claim for a "
                        f"{age}-year-old patient"
                    ),
                    "score": 20.0,
                    "evidence": {"icd11_code": code, "age": age},
                }

        return None

    async def _analyze_visit_frequency(self, sha_number: str) -> Dict[str, Any]:
        """
        Flag implausibly high facility-visit frequency in the last 30 days.
        Uses visit_admission_date instead of service_date.
        """
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)

        count = (
            self.db.query(func.count(Claim.id))
            .filter(
                Claim.patient_sha_number == sha_number,
                Claim.visit_admission_date >= thirty_days_ago,
            )
            .scalar()
            or 0
        )

        if count > 50:
            return {
                "is_excessive": True,
                "count": count,
                "score": 20.0,
                "description": (
                    f"SHA member has {count} claims in the last 30 days "
                    "(>50 is highly suspicious)"
                ),
            }
        if count > 30:
            return {
                "is_excessive": True,
                "count": count,
                "score": 15.0,
                "description": (
                    f"SHA member has {count} claims in the last 30 days "
                    "(>30 is suspicious)"
                ),
            }
        return {"is_excessive": False, "count": count}

    @staticmethod
    def _check_name_match(claim: Claim, registry_data: Dict[str, Any]) -> bool:
        """
        Loose name match: compare last name from the claim against the
        registry full_name. Returns True if names are consistent.
        """
        registry_name = (registry_data.get("full_name") or "").lower()
        claim_last = (claim.patient_last_name or "").lower().strip()
        claim_first = (claim.patient_first_name or "").lower().strip()

        if not registry_name or not claim_last:
            return True  # Cannot determine mismatch without both sides

        return claim_last in registry_name or claim_first in registry_name
