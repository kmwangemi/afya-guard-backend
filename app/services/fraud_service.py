"""
SHA Fraud Detection — Fraud Service (Hybrid Scoring Orchestrator)

Orchestrates the full fraud scoring pipeline for a claim:
  1. Feature engineering (via FeatureService)
  2. Rule engine (deterministic, DB-configurable rules)
  3. Modular detectors (Duplicate, Phantom, Upcoding, Provider)
  4. ML model scoring (XGBoost, with graceful fallback)
  5. Score aggregation (weighted formula)
  6. Explainability (SHAP-style feature weights stored per score)
  7. FraudScore + FraudExplanation persistence
  8. Auto FraudCase creation for HIGH/CRITICAL scores
  9. Auto FraudAlert generation
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.detectors.base_detector import DetectorResult
from app.detectors.duplicate_detector import DuplicateDetector
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


class FraudService:

    def __init__(self, db: AsyncSession):
        self.db = db
        self.detectors = [
            DuplicateDetector(db),
            PhantomPatientDetector(db),
            UpcodingDetector(db),
            ProviderProfiler(db),
        ]

    # ── Main entry point ──────────────────────────────────────────────────────

    async def score_claim(
        self,
        claim: Claim,
        scored_by: str = "system",
        triggered_by_user_id: Optional[uuid.UUID] = None,
    ) -> FraudScore:
        """
        Run the full fraud scoring pipeline for a single claim.
        Returns the persisted FraudScore with explanations.
        """

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
        # ── Step 10: Auto-raise FraudAlerts for fired detectors ───────────────
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
        """
        Run all active fraud rules against computed features.
        Returns (total_rule_score, explanation_dict).
        """
        result = await self.db.execute(
            select(FraudRule).filter(FraudRule.is_active == True)
        )
        active_rules = result.scalars().all()
        total_score: float = 0.0
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
        """
        Evaluate a single rule's JSONB config against claim features.
        Config format: {"field": "...", "operator": "...", "value": ...}
        """
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

    def _resolve_field(
        self,
        field: str,
        features: ClaimFeature,
        claim: Claim,
    ):
        """Resolve a field name to its value from features or claim object."""
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
        }
        claim_fields = {
            "total_claim_amount",
            "approved_amount",
            "claim_type",
            "sha_status",
        }
        if field in feature_fields and features:
            return getattr(features, field, None)
        elif field in claim_fields:
            return getattr(claim, field, None)
        return None

    # ── ML Model ──────────────────────────────────────────────────────────────

    async def _run_ml_model(
        self, features: ClaimFeature
    ) -> tuple[float, Dict[str, float], Optional[uuid.UUID]]:
        """
        Run XGBoost model if available.
        Falls back to rule-only scoring if model not loaded or not deployed.
        Returns (ml_score_0_to_100, shap_explanations, model_version_id).
        """
        try:
            import shap
            import xgboost as xgb

            result = await self.db.execute(
                select(ModelVersion).filter(ModelVersion.is_deployed == True)
            )
            model_ver = result.scalars().first()
            if not model_ver or not model_ver.model_artifact_path:
                return self._ml_fallback(features)
            model = xgb.XGBClassifier()
            model.load_model(model_ver.model_artifact_path)
            df = self._features_to_dataframe(features)
            prob = float(model.predict_proba(df)[0][1]) * 100
            explainer = shap.TreeExplainer(model)
            shap_values = explainer.shap_values(df)
            shap_map = {
                col: round(float(shap_values[0][i]), 6)
                for i, col in enumerate(df.columns)
            }
            return prob, shap_map, model_ver.id
        except Exception:
            return self._ml_fallback(features)

    def _ml_fallback(
        self, features: ClaimFeature
    ) -> tuple[float, Dict[str, float], None]:
        """Simple heuristic scoring when no ML model is deployed."""
        score: float = 0.0
        explanations: Dict[str, float] = {}
        if features.provider_cost_zscore and features.provider_cost_zscore > 2:
            score += 30.0
            explanations["provider_cost_zscore"] = features.provider_cost_zscore
        if features.member_visits_30d and features.member_visits_30d > 4:
            score += 20.0
            explanations["member_visits_30d"] = float(features.member_visits_30d)
        if features.diagnosis_cost_zscore and features.diagnosis_cost_zscore > 2:
            score += 25.0
            explanations["diagnosis_cost_zscore"] = features.diagnosis_cost_zscore
        if features.has_lab_without_diagnosis:
            score += 15.0
            explanations["has_lab_without_diagnosis"] = 1.0
        if features.has_surgery_without_theatre:
            score += 20.0
            explanations["has_surgery_without_theatre"] = 1.0
        return min(score, 100.0), explanations, None

    def _features_to_dataframe(self, features: ClaimFeature):
        """Convert ClaimFeature ORM object to pandas DataFrame for XGBoost."""
        import pandas as pd

        return pd.DataFrame(
            [
                {
                    "provider_avg_cost_90d": features.provider_avg_cost_90d or 0,
                    "provider_cost_zscore": features.provider_cost_zscore or 0,
                    "member_visits_30d": features.member_visits_30d or 0,
                    "member_visits_7d": features.member_visits_7d or 0,
                    "member_unique_providers_30d": features.member_unique_providers_30d
                    or 0,
                    "duplicate_within_7d": int(features.duplicate_within_7d or False),
                    "length_of_stay": features.length_of_stay or 0,
                    "weekend_submission": int(features.weekend_submission or False),
                    "diagnosis_cost_zscore": features.diagnosis_cost_zscore or 0,
                    "service_count": features.service_count or 0,
                    "has_lab_without_diagnosis": int(
                        features.has_lab_without_diagnosis or False
                    ),
                    "has_surgery_without_theatre": int(
                        features.has_surgery_without_theatre or False
                    ),
                }
            ]
        )

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
        # Score-based alert
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
        # Detector-specific alerts
        detector_alert_map = {
            "DuplicateDetector": AlertType.DUPLICATE_CLAIM,
            "PhantomPatientDetector": AlertType.PHANTOM_PATIENT,
            "UpcodingDetector": AlertType.UPCODING_DETECTED,
            "ProviderProfiler": AlertType.PROVIDER_ANOMALY,
        }
        for result in detector_results:
            if result.fired and result.score >= 50:
                alert_type = detector_alert_map.get(result.detector_name)
                if not alert_type:
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
