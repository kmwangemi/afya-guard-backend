import os
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score
from sklearn.model_selection import train_test_split
import asyncio
import shap
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, update

from app.core.config import settings
from app.models import Claim, ClaimStatus, MLModel, Provider


class MLService:
    """
    Machine Learning Service for fraud risk prediction.

    Feature changes from old version — all aligned to SHAClaimData fields:
      - claim_amount           →  total_claim_amount
      - service_date           →  visit_admission_date  (for length-of-stay calc)
      - is_new_visit (bool)    →  new_or_return_visit   (str: "New" | "Return")
      - is_referred (bool)     →  was_referred          (bool, same semantics)
      NEW features added:
      - accommodation_type  (high-value accommodation is a strong fraud signal)
      - has_preauth         (missing preauth on benefit lines is a risk flag)
      - bill_claim_ratio    (ratio of total_claim_amount / total_bill_amount)
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.model_dir = os.path.join(settings.BASE_DIR, "ml_models")
        os.makedirs(self.model_dir, exist_ok=True)

        self.numeric_features = [
            "total_claim_amount",
            "length_of_stay",
            "provider_rejection_rate",
            "patient_claim_count_30d",
            "bill_claim_ratio",
            "benefit_line_count",
        ]
        self.categorical_features = [
            "visit_type",
            "new_or_return_visit",
            "was_referred",
            "accommodation_type",
            "has_preauth",
        ]
        self.pipeline: Optional[Pipeline] = None

    # ── Feature extraction helpers ────────────────────────────────────────

    def _length_of_stay(self, claim: Claim) -> int:
        admission = claim.admission_date
        discharge = claim.discharge_date
        if admission and discharge:
            days = (discharge - admission).days
            return max(days, 0)
        return 0

    async def _provider_rejection_rate(self, claim: Claim) -> float:
        provider = claim.provider
        if not provider:
            result = await self.db.execute(select(Provider).filter(Provider.id == claim.provider_id))
            provider = result.scalars().first()
            
        if provider and (provider.total_claims_count or 0) > 0:
            return (provider.rejected_claims_count or 0) / provider.total_claims_count
        return 0.0

    async def _patient_claim_count_30d(self, claim: Claim) -> int:
        patient_sha = claim.raw_payload.get("sha_number") if claim.raw_payload else None
        admission_date = claim.admission_date
        if not patient_sha or not admission_date:
            return 0
        thirty_ago = admission_date - timedelta(days=30)
        result = await self.db.execute(
            select(func.count(Claim.id))
            .filter(
                Claim.raw_payload["sha_number"].as_string() == patient_sha,
                Claim.id != claim.id,
                Claim.admission_date >= thirty_ago,
            )
        )
        return result.scalar() or 0

    def _bill_claim_ratio(self, claim: Claim) -> float:
        """
        Ratio of total_claim_amount to total_bill_amount.
        Values > 1.0 are impossible (claim exceeds bill) — strong fraud signal.
        """
        raw = claim.raw_payload or {}
        bill = float(raw.get("total_bill_amount") or 0)
        clm = float(claim.total_claim_amount or 0)
        if bill > 0:
            return round(clm / bill, 4)
        return 1.0  # default to 1 (neutral) when bill is absent

    def _has_preauth(self, claim: Claim) -> str:
        """Returns 'yes' if all benefit lines have a preauth number, else 'no'."""
        raw = claim.raw_payload or {}
        lines = raw.get("benefit_lines") or []
        if not lines:
            return "no"
        return "yes" if all(line.get("preauth_no") for line in lines) else "no"

    def _accommodation_type_normalised(self, claim: Claim) -> str:
        raw = claim.raw_payload or {}
        accom = (raw.get("accommodation_type") or "unknown").lower().strip()
        # Bucket into risk tiers for the model
        if accom in {"icu", "hdu", "nicu", "burns"}:
            return "high_value"
        if accom in {"maternity", "nbu", "psychiatric unit", "isolation"}:
            return "medium_value"
        if accom in {
            "female medical",
            "male medical",
            "female surgical",
            "male surgical",
            "gynaecology",
        }:
            return "standard"
        return "unknown"

    async def _build_feature_row(self, claim: Claim) -> Dict[str, Any]:
        raw = claim.raw_payload or {}
        return {
            "total_claim_amount": float(claim.total_claim_amount or 0),
            "length_of_stay": self._length_of_stay(claim),
            "provider_rejection_rate": await self._provider_rejection_rate(claim),
            "patient_claim_count_30d": await self._patient_claim_count_30d(claim),
            "bill_claim_ratio": self._bill_claim_ratio(claim),
            "benefit_line_count": len(raw.get("benefit_lines") or []),
            "visit_type": (claim.claim_type or "unknown").lower(),
            "new_or_return_visit": (raw.get("new_or_return_visit") or "unknown").lower(),
            "was_referred": "yes" if raw.get("was_referred") else "no",
            "accommodation_type": self._accommodation_type_normalised(claim),
            "has_preauth": self._has_preauth(claim),
        }

    # ── Training ──────────────────────────────────────────────────────────

    async def _extract_training_data(self) -> pd.DataFrame:
        result = await self.db.execute(
            select(Claim)
            .filter(
                Claim.status.in_(
                    [
                        ClaimStatus.APPROVED,
                        ClaimStatus.REJECTED,
                        ClaimStatus.FLAGGED_CRITICAL,
                    ]
                )
            )
        )
        claims = result.scalars().all()
        if not claims:
            return pd.DataFrame()

        rows = []
        for claim in claims:
            row = await self._build_feature_row(claim)
            # Label: 1 = fraud/risk, 0 = clean
            row["target"] = (
                1
                if claim.status in (ClaimStatus.FLAGGED_CRITICAL, ClaimStatus.REJECTED)
                else 0
            )
            rows.append(row)

        return pd.DataFrame(rows)

    async def train_model(self) -> Dict[str, Any]:
        """Train and persist a new fraud detection model."""
        df = await self._extract_training_data()
        if df.empty or len(df) < 20:
            return {
                "status": "failed",
                "reason": "Insufficient training data (need ≥20 samples)",
            }

        X = df.drop("target", axis=1)
        y = df["target"]

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )

        numeric_pipe = Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median")),
                ("scaler", StandardScaler()),
            ]
        )
        categorical_pipe = Pipeline(
            [
                ("imputer", SimpleImputer(strategy="constant", fill_value="missing")),
                ("onehot", OneHotEncoder(handle_unknown="ignore")),
            ]
        )
        preprocessor = ColumnTransformer(
            [
                ("num", numeric_pipe, self.numeric_features),
                ("cat", categorical_pipe, self.categorical_features),
            ]
        )

        pipeline = Pipeline(
            [
                ("preprocessor", preprocessor),
                (
                    "classifier",
                    GradientBoostingClassifier(
                        n_estimators=200,
                        max_depth=4,
                        learning_rate=0.05,
                        random_state=42,
                    ),
                ),
            ]
        )

        pipeline.fit(X_train, y_train)
        y_pred = pipeline.predict(X_test)

        metrics = {
            "accuracy": float(accuracy_score(y_test, y_pred)),
            "precision": float(precision_score(y_test, y_pred, zero_division=0)),
            "recall": float(recall_score(y_test, y_pred, zero_division=0)),
            "f1_score": float(f1_score(y_test, y_pred, zero_division=0)),
        }

        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        model_filename = f"fraud_model_gb_{timestamp}.joblib"
        model_path = os.path.join(self.model_dir, model_filename)
        joblib.dump(pipeline, model_path)

        # Deactivate old models and register new one
        await self.db.execute(
            update(MLModel)
            .where(MLModel.is_active == True)
            .values({"is_active": False})
        )
        db_model = MLModel(
            model_name=f"fraud_gb_{timestamp}",
            model_type="gradient_boosting",
            model_version="v2.0",
            model_path=model_path,
            accuracy=metrics["accuracy"],
            precision=metrics["precision"],
            recall=metrics["recall"],
            f1_score=metrics["f1_score"],
            training_samples=len(df),
            training_date=datetime.utcnow(),
            features_used=self.numeric_features + self.categorical_features,
            is_active=True,
        )
        self.db.add(db_model)
        await self.db.commit()

        return {"status": "success", "model_id": db_model.id, "metrics": metrics}

    # ── Prediction ────────────────────────────────────────────────────────

    async def load_active_model(self) -> Optional[Pipeline]:
        result = await self.db.execute(
            select(MLModel)
            .filter(MLModel.is_active == True)
            .order_by(MLModel.created_at.desc())
        )
        record = result.scalars().first()
        if not record or not os.path.exists(record.model_path):
            return None
        try:
            return joblib.load(record.model_path)
        except Exception as exc:
            print(f"[MLService] Error loading model: {exc}")
            return None

    async def predict_claim_risk(self, claim: Claim) -> Dict[str, Any]:
        """Return a fraud probability score (0–100) for a single claim."""
        model = await self.load_active_model()
        if not model:
            return {"error": "No active model available", "risk_score": 0.0}

        feature_row = await self._build_feature_row(claim)
        input_df = pd.DataFrame([feature_row])

        try:
            probability = model.predict_proba(input_df)[0][1]
            
            # --- SHAP Explainability ---
            explanation = []
            try:
                classifier = model.named_steps.get("classifier")
                preprocessor = model.named_steps.get("preprocessor")
                
                if classifier and preprocessor:
                    X_transformed = preprocessor.transform(input_df)
                    explainer = shap.TreeExplainer(classifier)
                    shap_values = explainer.shap_values(X_transformed)
                    
                    if isinstance(shap_values, list):
                        shap_vals = shap_values[1][0]
                    else:
                        shap_vals = shap_values[0]
                        
                    feature_names = preprocessor.get_feature_names_out()
                    feature_importance = [(feat, float(val)) for feat, val in zip(feature_names, shap_vals) if val != 0]
                    feature_importance.sort(key=lambda x: abs(x[1]), reverse=True)
                    
                    for feat, val in feature_importance[:3]:
                        clean_feat = feat.split("__")[-1] if "__" in feat else feat
                        impact_direction = "increased risk" if val > 0 else "decreased risk"
                        explanation.append({
                            "feature": clean_feat,
                            "impact": impact_direction,
                            "weight": round(float(val), 4)
                        })
            except Exception as shap_exc:
                print(f"[MLService] SHAP explainability error: {shap_exc}")

            return {
                "risk_score": round(probability * 100.0, 2),
                "model_used": "gradient_boosting_v2",
                "probability": round(float(probability), 6),
                "features": input_df.to_dict(orient="records")[0],
                "explainability": explanation
            }
        except Exception as exc:
            print(f"[MLService] Prediction error: {exc}")
            return {"error": str(exc), "risk_score": 0.0}
