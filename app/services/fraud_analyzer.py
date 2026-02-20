# ===========================================================================
# fraud_analyzer.py  (orchestrator)
# ===========================================================================

import time
import asyncio
from sqlalchemy.orm import Session
from datetime import datetime
from app.models import Claim, FraudAlert, ClaimStatus
from app.services.ml_service import MLService


class FraudAnalyzer:
    """
    Orchestrate all fraud detection modules and produce a composite risk score.

    Changes from old version:
      - MLService features updated to use visit_admission_date, total_claim_amount,
        accommodation_type, was_referred (see MLService below)
      - composite_risk_score uses weighted average instead of raw max,
        with max as a hard floor for critical signals
      - _create_fraud_alerts accepts a db session explicitly (no implicit commit race)
    """

    # Module weights for weighted-average composite score
    MODULE_WEIGHTS = {
        "phantom_patient": 1.0,
        "upcoding": 1.0,
        "duplicate": 1.0,
        "provider_risk": 0.5,  # Supporting signal, not primary evidence
        "ml_model": 0.7,  # ML score is useful but not deterministic
    }

    def __init__(self, db: Session):
        self.db = db

    async def analyze_claim(self, claim: Claim) -> Dict[str, Any]:
        """Run all detection modules and return a full analysis result."""
        start_time = time.time()

        results: Dict[str, Any] = {
            "claim_id": claim.id,
            "claim_number": claim.claim_number,
            "modules": {},
            "composite_risk_score": 0.0,
            "final_status": None,
            "recommendation": "",
            "total_execution_time_ms": 0.0,
            "ml_score": 0.0,
            "analyzed_at": None,
        }

        # ── ML prediction (synchronous CPU-bound, run first) ──────────────
        ml_service = MLService(self.db)
        ml_result = ml_service.predict_claim_risk(claim)
        ml_score = ml_result.get("risk_score", 0.0)
        results["ml_score"] = ml_score
        results["modules"]["ml_model"] = {
            "module": "ml_model",
            "risk_score": ml_score,
            "flags": (
                [
                    {
                        "type": "ml_predicted_risk",
                        "severity": "high",
                        "score": ml_score,
                    }
                ]
                if ml_score > 60
                else []
            ),
            "is_high_risk": ml_score > 60,
            "details": ml_result,
        }

        # ── Rule-based modules run in parallel ───────────────────────────
        module_tasks = [
            self._run_phantom_detection(claim),
            self._run_upcoding_detection(claim),
            self._run_duplicate_detection(claim),
            self._run_provider_profiling(claim),
        ]
        module_results = await asyncio.gather(*module_tasks, return_exceptions=True)
        module_names = ["phantom_patient", "upcoding", "duplicate", "provider_risk"]

        for name, result in zip(module_names, module_results):
            if isinstance(result, Exception):
                results["modules"][name] = {
                    "module": name,
                    "risk_score": 0.0,
                    "flags": [],
                    "is_high_risk": False,
                    "error": str(result),
                }
            else:
                results["modules"][name] = result

        # ── Composite score: weighted average with max as hard floor ──────
        weighted_sum = 0.0
        weight_total = 0.0
        max_score = 0.0

        for mod_name, weight in self.MODULE_WEIGHTS.items():
            score = results["modules"].get(mod_name, {}).get("risk_score", 0.0)
            weighted_sum += score * weight
            weight_total += weight
            max_score = max(max_score, score)

        weighted_avg = weighted_sum / weight_total if weight_total else 0.0
        # Use the higher of weighted average or 80% of the max single-module score
        # This ensures a single critical signal (e.g. exact duplicate = 100) is not
        # diluted too heavily by clean modules
        results["composite_risk_score"] = round(max(weighted_avg, max_score * 0.8), 2)

        results["final_status"] = self._determine_status(
            results["composite_risk_score"]
        )
        results["recommendation"] = self._get_recommendation(
            results["composite_risk_score"]
        )
        results["analyzed_at"] = datetime.utcnow()

        await self._create_fraud_alerts(claim, results)

        results["total_execution_time_ms"] = round((time.time() - start_time) * 1000, 2)
        return results

    # ── Module runners ────────────────────────────────────────────────────

    async def _run_phantom_detection(self, claim: Claim) -> Dict[str, Any]:
        try:
            return await PhantomPatientDetector(self.db).analyze_claim(claim)
        except Exception as exc:
            return self._error_module("phantom_patient", exc)

    async def _run_upcoding_detection(self, claim: Claim) -> Dict[str, Any]:
        try:
            return await UpcodingDetector(self.db).analyze_claim(claim)
        except Exception as exc:
            return self._error_module("upcoding", exc)

    async def _run_duplicate_detection(self, claim: Claim) -> Dict[str, Any]:
        try:
            return await DuplicateDetector(self.db).analyze_claim(claim)
        except Exception as exc:
            return self._error_module("duplicate", exc)

    async def _run_provider_profiling(self, claim: Claim) -> Dict[str, Any]:
        try:
            return await ProviderProfiler(self.db).analyze_claim(claim)
        except Exception as exc:
            return self._error_module("provider_risk", exc)

    @staticmethod
    def _error_module(name: str, exc: Exception) -> Dict[str, Any]:
        return {
            "module": name,
            "risk_score": 0.0,
            "flags": [],
            "is_high_risk": False,
            "error": str(exc),
        }

    # ── Scoring helpers ───────────────────────────────────────────────────

    @staticmethod
    def _determine_status(risk_score: float) -> ClaimStatus:
        if risk_score >= 80:
            return ClaimStatus.FLAGGED_CRITICAL
        if risk_score >= 60:
            return ClaimStatus.FLAGGED_REVIEW
        if risk_score < 40:
            return ClaimStatus.AUTO_APPROVED
        return ClaimStatus.PENDING

    @staticmethod
    def _get_recommendation(risk_score: float) -> str:
        if risk_score >= 80:
            return "REJECT — Critical fraud indicators detected. Immediate investigation required."
        if risk_score >= 60:
            return (
                "HOLD — High risk. Manual review and additional documentation required."
            )
        if risk_score >= 40:
            return "REVIEW — Medium risk. Standard verification procedures apply."
        return "APPROVE — Low risk. Process for payment."

    async def _create_fraud_alerts(self, claim: Claim, results: Dict[str, Any]) -> None:
        """Upsert a FraudAlert for claims scoring ≥ 60."""
        if results["composite_risk_score"] < 60:
            return

        severity = "critical" if results["composite_risk_score"] >= 80 else "high"

        existing = (
            self.db.query(FraudAlert).filter(FraudAlert.claim_id == claim.id).first()
        )
        if existing:
            existing.severity = severity
            existing.evidence = results
            existing.status = "open"
        else:
            self.db.add(
                FraudAlert(
                    claim_id=claim.id,
                    alert_type="multiple_indicators",
                    severity=severity,
                    description=(
                        f"Multiple fraud indicators detected. "
                        f"Risk score: {results['composite_risk_score']:.1f}"
                    ),
                    evidence=results,
                    detection_module="fraud_analyzer",
                    module_confidence=results["composite_risk_score"] / 100.0,
                    status="open",
                    priority="critical" if severity == "critical" else "high",
                )
            )

        self.db.commit()


# ===========================================================================
# background task entry point
# ===========================================================================


async def analyze_claim_background(claim_id: int) -> None:
    """
    Background task entry point called from the FastAPI router.
    Opens its own DB session, runs full analysis, writes results back to Claim.
    """
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        claim = db.query(Claim).filter(Claim.id == claim_id).first()
        if not claim:
            print(f"[fraud_analyzer] Claim {claim_id} not found — skipping")
            return

        analyzer = FraudAnalyzer(db)
        result = await analyzer.analyze_claim(claim)

        claim.risk_score = result["composite_risk_score"]
        claim.is_flagged = result["composite_risk_score"] >= 60
        claim.status = result["final_status"]
        claim.fraud_flags = [
            flag
            for module_result in result["modules"].values()
            for flag in (module_result.get("flags") or [])
        ]
        claim.analysis_completed_at = result["analyzed_at"]
        db.commit()

        print(
            f"[fraud_analyzer] Claim {claim_id} analysed — "
            f"score: {result['composite_risk_score']:.1f} | "
            f"status: {result['final_status']}"
        )

    except Exception as exc:
        print(f"[fraud_analyzer] Error analysing claim {claim_id}: {exc}")
        db.rollback()
    finally:
        db.close()
