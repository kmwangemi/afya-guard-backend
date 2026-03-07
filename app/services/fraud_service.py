"""
SHA Fraud Detection — Fraud Service (Hybrid Scoring Orchestrator)

Orchestrates the full fraud scoring pipeline for a claim:
  1. Feature engineering (via FeatureService)
  2. Rule engine (deterministic, DB-configurable rules)
  3. Modular detectors (Duplicate, Phantom, Upcoding, Provider, GhostProvider)
  4. ML model scoring (XGBoost, with graceful fallback)
  5. Score aggregation (weighted formula)
  6. Explainability (SHAP-style feature weights stored per score)
  7. FraudScore + FraudExplanation persistence
  8. Auto FraudCase creation for HIGH/CRITICAL scores
  9. Auto FraudAlert generation
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Dict, List, Optional

import joblib
import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.detectors.base_detector import DetectorResult

# FIX 1: All detector imports grouped together
from app.detectors.duplicate_detector import DuplicateDetector
from app.detectors.ghost_provider_detector import GhostProviderDetector
from app.detectors.phantom_patient_detector import PhantomPatientDetector
from app.detectors.provider_profiler_detector import ProviderProfiler
from app.detectors.upcoding_detector import UpcodingDetector
from app.models.claim_feature_model import ClaimFeature
from app.models.claim_model import Claim
from app.models.enums_model import (
    AlertSeverity,
    AlertStatus,
    AlertType,
    AuditAction,
    CasePriority,
    CaseStatus,
    RiskLevel,
)
from app.models.fraud_alert_model import FraudAlert
from app.models.fraud_case_model import FraudCase
from app.models.fraud_explanation_model import FraudExplanation
from app.models.fraud_rule_model import FraudRule
from app.models.fraud_score_model import FraudScore
from app.models.model_version_model import ModelVersion
from app.services.audit_service import AuditService
from app.services.feature_service import FeatureService
from app.utils.provider_utils import parse_facility_level

logger = logging.getLogger(__name__)

# ── Module-level model cache ──────────────────────────────────────────────────
_xgb_model: Optional[object] = None
_feature_list: Optional[list] = None


def load_ml_artifacts() -> None:
    """
    Load XGBoost model and feature list from disk into module-level cache.
    Call this ONCE from your FastAPI lifespan startup hook:

        from app.services.fraud_service import load_ml_artifacts
        load_ml_artifacts()

    Safe to call multiple times — skips reload if already loaded.
    """
    global _xgb_model, _feature_list

    model_dir = settings.MODEL_DIR
    model_path = model_dir / "fraud_xgboost.joblib"
    feature_path = model_dir / "feature_list.joblib"

    if not model_path.exists():
        logger.warning(
            f"ML model not found at {model_path} — will use fallback scoring"
        )
        return
    if _xgb_model is not None:
        logger.debug("ML model already loaded — skipping reload")
        return
    try:
        _xgb_model = joblib.load(model_path)
        _feature_list = joblib.load(feature_path)
        logger.info(f"ML model loaded: {model_path} | features: {len(_feature_list)}")
    except Exception as exc:
        logger.error(f"Failed to load ML model: {exc}")
        _xgb_model = None
        _feature_list = None


# FIX 2: Detector → AlertType map defined at module level, includes GhostProviderDetector.
# Defined here (not inside _raise_alerts) so it's easy to extend without hunting
# through method bodies. Add AlertType.GHOST_PROVIDER to your enum — see FIX 3.
DETECTOR_ALERT_MAP: Dict[str, AlertType] = {
    "DuplicateDetector": AlertType.DUPLICATE_CLAIM,
    "PhantomPatientDetector": AlertType.PHANTOM_PATIENT,
    "UpcodingDetector": AlertType.UPCODING_DETECTED,
    "ProviderProfiler": AlertType.PROVIDER_ANOMALY,
    "GhostProviderDetector": AlertType.GHOST_PROVIDER,  # FIX 2 — was missing
}


class FraudService:

    def __init__(self, db: AsyncSession):
        self.db = db
        self.detectors = [
            DuplicateDetector(db),
            PhantomPatientDetector(db),
            UpcodingDetector(db),
            ProviderProfiler(db),
            GhostProviderDetector(db),
        ]

    # ── Main entry point ──────────────────────────────────────────────────────

    async def score_claim(
        self,
        claim: Claim,
        scored_by: str = "system",
        triggered_by_user_id: Optional[uuid.UUID] = None,
    ) -> FraudScore:
        # ── Step 1: Feature engineering ───────────────────────────────────────
        features = await FeatureService.compute_features(
            self.db, claim, triggered_by=triggered_by_user_id
        )
        # ── Step 2: Rule engine ───────────────────────────────────────────────
        rule_score, rule_explanations = await self._run_rule_engine(features, claim)
        # ── Step 3: Modular detectors ─────────────────────────────────────────
        detector_results: List[DetectorResult] = []
        for detector in self.detectors:
            result = await detector.detect(claim, features)
            detector_results.append(result)
        detector_scores: Dict[str, float] = {
            r.detector_name: r.score for r in detector_results
        }
        avg_detector_score = (
            sum(detector_scores.values()) / len(detector_scores)
            if detector_scores
            else 0.0
        )
        # ── Step 4: ML scoring ────────────────────────────────────────────────
        ml_score, ml_explanations, model_version_id = await self._run_ml_model(features)
        # ── Step 5: Aggregate final score ─────────────────────────────────────
        final_score = (
            rule_score * settings.RULE_SCORE_WEIGHT
            + ml_score * settings.ML_SCORE_WEIGHT
            + avg_detector_score * settings.DETECTOR_SCORE_WEIGHT
        )
        final_score = round(min(final_score, 100.0), 4)
        # ── Step 6: Determine risk level ──────────────────────────────────────
        risk_level = self._determine_risk_level(final_score)
        # ── Step 7: Persist FraudScore ────────────────────────────────────────
        fraud_score = FraudScore(
            claim_id=claim.id,
            rule_score=round(rule_score, 4),
            ml_probability=round(ml_score, 4),
            anomaly_score=None,
            detector_scores=detector_scores,
            final_score=final_score,
            risk_level=risk_level,
            scored_at=datetime.now(UTC),
            scored_by=scored_by,
            model_version_id=model_version_id,
        )
        self.db.add(fraud_score)
        await self.db.flush()
        # ── Step 8: Persist Explanations ──────────────────────────────────────
        all_explanations = []
        for feat, weight in rule_explanations.items():
            all_explanations.append(
                FraudExplanation(
                    fraud_score_id=fraud_score.id,
                    explanation=f"Rule: {feat}",
                    feature_name=feat,
                    feature_value=str(weight),
                    weight=float(weight),
                    source="rule_engine",
                )
            )
        for feat, weight in ml_explanations.items():
            all_explanations.append(
                FraudExplanation(
                    fraud_score_id=fraud_score.id,
                    explanation=f"ML feature: {feat}",
                    feature_name=feat,
                    feature_value=str(weight),
                    weight=float(weight),
                    source="ml_model",
                )
            )
        for result in detector_results:
            if result.fired:
                for detector in self.detectors:
                    if detector.name == result.detector_name:
                        await detector.explain(claim, features)
                        break
                all_explanations.append(
                    FraudExplanation(
                        fraud_score_id=fraud_score.id,
                        explanation=result.explanation,
                        feature_name=result.feature_name,
                        feature_value=result.feature_value,
                        weight=round(result.score, 4),
                        source=result.detector_name,
                    )
                )
        self.db.add_all(all_explanations)
        # ── Step 9: Auto-create FraudCase if HIGH/CRITICAL ────────────────────
        if risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            await self._auto_create_case(claim, fraud_score, risk_level)
        # ── Step 10: Auto-raise FraudAlerts ───────────────────────────────────
        await self._raise_alerts(claim, fraud_score, detector_results, risk_level)
        await self.db.commit()
        await self.db.refresh(fraud_score)
        await AuditService.log(
            self.db,
            AuditAction.CLAIM_SCORED,
            user_id=triggered_by_user_id,
            entity_type="FraudScore",
            entity_id=fraud_score.id,
            metadata={
                "claim_id": str(claim.id),
                "final_score": final_score,
                "risk_level": risk_level,
                "rule_score": rule_score,
                "ml_score": ml_score,
                "detector_scores": detector_scores,
            },
        )
        return fraud_score

    # ── Rule Engine ───────────────────────────────────────────────────────────

    async def _run_rule_engine(
        self,
        features: ClaimFeature,
        claim: Claim,
    ) -> tuple[float, Dict[str, float]]:
        result = await self.db.execute(
            select(FraudRule).filter(FraudRule.is_active == True)  # noqa: E712
        )
        active_rules = result.scalars().all()
        total_score = 0.0
        explanations: Dict[str, float] = {}
        for rule in active_rules:
            fired, contribution = self._evaluate_rule(rule, features, claim)
            if fired:
                total_score += contribution
                explanations[rule.rule_name] = contribution
        return min(total_score, 100.0), explanations

    def _evaluate_rule(
        self,
        rule: FraudRule,
        features: ClaimFeature,
        claim: Claim,
    ) -> tuple[bool, float]:
        config = rule.config or {}
        field = config.get("field")
        operator = config.get("operator")
        threshold = config.get("value")
        if not field or not operator:
            return False, 0.0
        actual = self._resolve_field(field, features, claim)
        if actual is None:
            return False, 0.0
        fired = False
        try:
            if operator == "equals":
                fired = actual == threshold
            elif operator == "not_equals":
                fired = actual != threshold
            elif operator == "greater_than":
                fired = float(actual) > float(threshold)
            elif operator == "less_than":
                fired = float(actual) < float(threshold)
            elif operator == "greater_or_equal":
                fired = float(actual) >= float(threshold)
            elif operator == "less_or_equal":
                fired = float(actual) <= float(threshold)
            elif operator == "is_true":
                fired = bool(actual) is True
            elif operator == "is_false":
                fired = bool(actual) is False
            elif operator == "in":
                fired = actual in (threshold or [])
            elif operator == "not_in":
                fired = actual not in (threshold or [])
        except (TypeError, ValueError):
            return False, 0.0
        return fired, float(rule.weight) if fired else 0.0

    def _resolve_field(self, field: str, features: ClaimFeature, claim: Claim):
        feature_fields = {
            "provider_avg_cost_90d",
            "provider_cost_zscore",
            "member_visits_30d",
            "member_visits_7d",
            "member_unique_providers_30d",
            "duplicate_within_7d",
            "length_of_stay",
            "weekend_submission",
            "diagnosis_cost_zscore",
            "service_count",
            "has_lab_without_diagnosis",
            "has_surgery_without_theatre",
            "submitted_hour",
            "eligibility_checked",
        }
        claim_fields = {
            "total_claim_amount",
            "approved_amount",
            "claim_type",
            "sha_status",
        }
        if field in feature_fields and features:
            return getattr(features, field, None)
        if field in claim_fields:
            return getattr(claim, field, None)
        if claim:
            amount = float(claim.total_claim_amount or 0)
            los = float(features.length_of_stay or 0) if features else 0.0
            svc_count = int(features.service_count or 1) if features else 1
            provider = getattr(claim, "provider", None)
            fac_level = parse_facility_level(provider)
            s_hour = (
                int(getattr(features, "submitted_hour", 12) or 12) if features else 12
            )
            computed = {
                "facility_level": fac_level,
                "log_amount": float(np.log1p(amount)),
                "amount_per_service": round(amount / max(svc_count, 1), 2),
                "amount_per_day": round(amount / max(los, 1), 2),
                "is_off_hours": int(s_hour >= 23 or s_hour <= 5),
                "no_eligibility_check": int(
                    not bool(getattr(features, "eligibility_checked", True))
                ),
                "high_service_count": int(svc_count > 8),
                "level_amount_mismatch": int(fac_level <= 2 and amount > 10000),
            }
            if field in computed:
                return computed[field]
        return None

    # ── ML Model ──────────────────────────────────────────────────────────────

    async def _run_ml_model(
        self, features: ClaimFeature
    ) -> tuple[float, Dict[str, float], Optional[uuid.UUID]]:
        if _xgb_model is None:
            logger.debug("ML model not loaded — using fallback scoring")
            return self._ml_fallback(features)
        try:
            result = await self.db.execute(
                select(ModelVersion).filter(
                    ModelVersion.is_deployed == True
                )  # noqa: E712
            )
            model_ver = result.scalars().first()
            df = self._features_to_dataframe(features)
            prob = float(_xgb_model.predict_proba(df)[0][1]) * 100
            feature_list = _feature_list or list(df.columns)
            importances = _xgb_model.feature_importances_
            explanation_map = {
                col: round(float(importances[i]), 6)
                for i, col in enumerate(feature_list)
            }
            return prob, explanation_map, model_ver.id if model_ver else None
        except Exception as exc:
            logger.warning(f"ML scoring failed, using fallback: {exc}")
            return self._ml_fallback(features)

    def _ml_fallback(
        self, features: ClaimFeature
    ) -> tuple[float, Dict[str, float], None]:
        score: float = 0.0
        explanations: Dict[str, float] = {}
        if features.provider_cost_zscore and features.provider_cost_zscore > 2:
            score += 25.0
            explanations["provider_cost_zscore"] = float(features.provider_cost_zscore)
        if features.member_visits_30d and features.member_visits_30d > 4:
            score += 15.0
            explanations["member_visits_30d"] = float(features.member_visits_30d)
        if features.member_visits_7d and features.member_visits_7d > 2:
            score += 10.0
            explanations["member_visits_7d"] = float(features.member_visits_7d)
        if (
            features.member_unique_providers_30d
            and features.member_unique_providers_30d > 3
        ):
            score += 15.0
            explanations["member_unique_providers_30d"] = float(
                features.member_unique_providers_30d
            )
        if features.duplicate_within_7d:
            score += 30.0
            explanations["duplicate_within_7d"] = 1.0
        if features.diagnosis_cost_zscore and features.diagnosis_cost_zscore > 2:
            score += 20.0
            explanations["diagnosis_cost_zscore"] = float(
                features.diagnosis_cost_zscore
            )
        if features.has_lab_without_diagnosis:
            score += 15.0
            explanations["has_lab_without_diagnosis"] = 1.0
        if features.has_surgery_without_theatre:
            score += 20.0
            explanations["has_surgery_without_theatre"] = 1.0
        s_hour = int(features.submitted_hour or 12)
        if s_hour >= 23 or s_hour <= 5:
            score += 10.0
            explanations["is_off_hours"] = 1.0
        return min(score, 100.0), explanations, None

    def _features_to_dataframe(self, features: ClaimFeature):
        import pandas as pd

        claim = features.claim
        provider = getattr(claim, "provider", None) if claim else None
        amount = float(claim.total_claim_amount or 0) if claim else 0.0
        los = float(features.length_of_stay or 0)
        svc_count = int(features.service_count or 1)
        fac_level = parse_facility_level(provider) if provider else 4
        claim_type_raw = (claim.claim_type or "OUTPATIENT") if claim else "OUTPATIENT"
        claim_type_enc = 0 if str(claim_type_raw).upper() == "INPATIENT" else 1
        county = getattr(provider, "county", "Nairobi") or "Nairobi"
        county_enc = hash(county) % 10
        submitted_hour = int(features.submitted_hour or 12)
        row = {
            "provider_avg_cost_90d": float(features.provider_avg_cost_90d or 0),
            "provider_cost_zscore": float(features.provider_cost_zscore or 0),
            "member_visits_30d": int(features.member_visits_30d or 0),
            "member_visits_7d": int(features.member_visits_7d or 0),
            "member_unique_providers_30d": int(
                features.member_unique_providers_30d or 0
            ),
            "duplicate_within_7d": int(bool(features.duplicate_within_7d)),
            "length_of_stay": los,
            "weekend_submission": int(bool(features.weekend_submission)),
            "diagnosis_cost_zscore": float(features.diagnosis_cost_zscore or 0),
            "service_count": svc_count,
            "has_lab_without_diagnosis": int(bool(features.has_lab_without_diagnosis)),
            "has_surgery_without_theatre": int(
                bool(features.has_surgery_without_theatre)
            ),
            "claim_type_enc": claim_type_enc,
            "county_enc": county_enc,
            "facility_level": fac_level,
            "log_amount": float(np.log1p(amount)),
            "amount_per_service": round(amount / max(svc_count, 1), 2),
            "amount_per_day": round(amount / max(los, 1), 2),
            "submitted_hour": submitted_hour,
            "is_off_hours": int(submitted_hour >= 23 or submitted_hour <= 5),
            "no_eligibility_check": int(
                not bool(getattr(features, "eligibility_checked", True))
            ),
            "high_service_count": int(svc_count > 8),
            "level_amount_mismatch": int(fac_level <= 2 and amount > 10000),
        }
        feature_list = _feature_list or list(row.keys())
        return pd.DataFrame([row])[feature_list]

    # ── Risk level ────────────────────────────────────────────────────────────

    def _determine_risk_level(self, score: float) -> RiskLevel:
        if score >= settings.FRAUD_CRITICAL_THRESHOLD:
            return RiskLevel.CRITICAL
        elif score >= settings.FRAUD_HIGH_THRESHOLD:
            return RiskLevel.HIGH
        elif score >= settings.FRAUD_MEDIUM_THRESHOLD:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    # ── Auto case creation ────────────────────────────────────────────────────

    async def _auto_create_case(
        self,
        claim: Claim,
        fraud_score: FraudScore,
        risk_level: RiskLevel,
    ) -> Optional[FraudCase]:
        result = await self.db.execute(
            select(FraudCase).filter(FraudCase.claim_id == claim.id)
        )
        existing = result.scalars().first()
        if existing:
            return existing
        priority = (
            CasePriority.URGENT
            if risk_level == RiskLevel.CRITICAL
            else CasePriority.HIGH
        )
        case = FraudCase(
            claim_id=claim.id,
            fraud_score_id=fraud_score.id,
            status=CaseStatus.OPEN,
            priority=priority,
        )
        self.db.add(case)
        await self.db.flush()
        return case

    # ── Auto alert generation ─────────────────────────────────────────────────

    async def _raise_alerts(
        self,
        claim: Claim,
        fraud_score: FraudScore,
        detector_results: List[DetectorResult],
        risk_level: RiskLevel,
    ) -> None:
        alerts_to_add = []

        # High/Critical score alert
        if risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            severity = (
                AlertSeverity.CRITICAL
                if risk_level == RiskLevel.CRITICAL
                else AlertSeverity.HIGH
            )
            alert_type = (
                AlertType.CRITICAL_RISK_SCORE
                if risk_level == RiskLevel.CRITICAL
                else AlertType.HIGH_RISK_SCORE
            )
            alerts_to_add.append(
                FraudAlert(
                    claim_id=claim.id,
                    fraud_score_id=fraud_score.id,
                    alert_type=alert_type,
                    severity=severity,
                    status=AlertStatus.OPEN,
                    title=f"{risk_level} Risk Claim Detected",
                    message=(
                        f"Claim {claim.sha_claim_id} scored {fraud_score.final_score:.1f}/100 "
                        f"({risk_level} risk). Immediate review recommended."
                    ),
                    triggered_by="FraudService",
                    score_at_alert=fraud_score.final_score,
                    auto_escalate=risk_level == RiskLevel.CRITICAL,
                    auto_escalate_after_hours=settings.ALERT_AUTO_ESCALATE_HOURS,
                    expires_at=datetime.now(UTC)
                    + timedelta(hours=settings.ALERT_EXPIRE_HOURS),
                )
            )

        # FIX 2: Use module-level DETECTOR_ALERT_MAP — includes GhostProviderDetector.
        # Old code had a local dict inside this method that was missing the entry,
        # causing ghost provider alerts to be silently dropped.
        for result in detector_results:
            if not result.fired or result.score < 50:
                continue
            alert_type = DETECTOR_ALERT_MAP.get(result.detector_name)
            if not alert_type:
                # FIX 3: Warn loudly instead of silently skipping unknown detectors.
                # If you add a new detector and forget to add it to DETECTOR_ALERT_MAP,
                # you'll see this in logs rather than wondering why no alerts appear.
                logger.warning(
                    f"No AlertType mapping for detector '{result.detector_name}' — "
                    f"alert not raised. Add it to DETECTOR_ALERT_MAP at module level."
                )
                continue
            alerts_to_add.append(
                FraudAlert(
                    claim_id=claim.id,
                    fraud_score_id=fraud_score.id,
                    alert_type=alert_type,
                    severity=(
                        AlertSeverity.WARNING
                        if result.score < 75
                        else AlertSeverity.HIGH
                    ),
                    status=AlertStatus.OPEN,
                    title=f"{result.detector_name} Alert — {claim.sha_claim_id}",
                    message=result.explanation,
                    triggered_by=result.detector_name,
                    score_at_alert=fraud_score.final_score,
                    auto_escalate=False,
                    expires_at=datetime.now(UTC)
                    + timedelta(hours=settings.ALERT_EXPIRE_HOURS),
                    metadata=result.metadata,
                )
            )

        if alerts_to_add:
            self.db.add_all(alerts_to_add)
