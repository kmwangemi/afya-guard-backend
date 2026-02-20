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
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

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

    def __init__(self, db: Session):
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
        admission = claim.visit_admission_date
        discharge = claim.discharge_date
        if admission and discharge:
            days = (discharge - admission).days
            return max(days, 0)
        return 0

    def _provider_rejection_rate(self, claim: Claim) -> float:
        provider = claim.provider or (
            self.db.query(Provider).filter(Provider.id == claim.provider_id).first()
        )
        if provider and (provider.total_claims_count or 0) > 0:
            return (provider.rejected_claims_count or 0) / provider.total_claims_count
        return 0.0

    def _patient_claim_count_30d(self, claim: Claim) -> int:
        if not claim.patient_sha_number or not claim.visit_admission_date:
            return 0
        thirty_ago = claim.visit_admission_date - timedelta(days=30)
        return (
            self.db.query(func.count(Claim.id))
            .filter(
                Claim.patient_sha_number == claim.patient_sha_number,
                Claim.id != claim.id,
                Claim.visit_admission_date >= thirty_ago,
            )
            .scalar()
            or 0
        )

    def _bill_claim_ratio(self, claim: Claim) -> float:
        """
        Ratio of total_claim_amount to total_bill_amount.
        Values > 1.0 are impossible (claim exceeds bill) — strong fraud signal.
        """
        bill = float(claim.total_bill_amount or 0)
        clm = float(claim.total_claim_amount or 0)
        if bill > 0:
            return round(clm / bill, 4)
        return 1.0  # default to 1 (neutral) when bill is absent

    def _has_preauth(self, claim: Claim) -> str:
        """Returns 'yes' if all benefit lines have a preauth number, else 'no'."""
        lines = claim.benefit_lines or []
        if not lines:
            return "no"
        return "yes" if all(line.get("preauth_no") for line in lines) else "no"

    def _accommodation_type_normalised(self, claim: Claim) -> str:
        accom = (claim.accommodation_type or "unknown").lower().strip()
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

    def _build_feature_row(self, claim: Claim) -> Dict[str, Any]:
        return {
            "total_claim_amount": float(claim.total_claim_amount or 0),
            "length_of_stay": self._length_of_stay(claim),
            "provider_rejection_rate": self._provider_rejection_rate(claim),
            "patient_claim_count_30d": self._patient_claim_count_30d(claim),
            "bill_claim_ratio": self._bill_claim_ratio(claim),
            "benefit_line_count": len(claim.benefit_lines or []),
            "visit_type": (claim.visit_type or "unknown").lower(),
            "new_or_return_visit": (claim.new_or_return_visit or "unknown").lower(),
            "was_referred": "yes" if claim.was_referred else "no",
            "accommodation_type": self._accommodation_type_normalised(claim),
            "has_preauth": self._has_preauth(claim),
        }

    # ── Training ──────────────────────────────────────────────────────────

    def _extract_training_data(self) -> pd.DataFrame:
        claims = (
            self.db.query(Claim)
            .filter(
                Claim.status.in_(
                    [
                        ClaimStatus.APPROVED,
                        ClaimStatus.REJECTED,
                        ClaimStatus.FLAGGED_CRITICAL,
                    ]
                )
            )
            .all()
        )
        if not claims:
            return pd.DataFrame()

        rows = []
        for claim in claims:
            row = self._build_feature_row(claim)
            # Label: 1 = fraud/risk, 0 = clean
            row["target"] = (
                1
                if claim.status in (ClaimStatus.FLAGGED_CRITICAL, ClaimStatus.REJECTED)
                else 0
            )
            rows.append(row)

        return pd.DataFrame(rows)

    def train_model(self) -> Dict[str, Any]:
        """Train and persist a new fraud detection model."""
        df = self._extract_training_data()
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
        self.db.query(MLModel).filter(MLModel.is_active == True).update(
            {"is_active": False}
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
        self.db.commit()

        return {"status": "success", "model_id": db_model.id, "metrics": metrics}

    # ── Prediction ────────────────────────────────────────────────────────

    def load_active_model(self) -> Optional[Pipeline]:
        record = (
            self.db.query(MLModel)
            .filter(MLModel.is_active == True)
            .order_by(MLModel.created_at.desc())
            .first()
        )
        if not record or not os.path.exists(record.model_path):
            return None
        try:
            return joblib.load(record.model_path)
        except Exception as exc:
            print(f"[MLService] Error loading model: {exc}")
            return None

    def predict_claim_risk(self, claim: Claim) -> Dict[str, Any]:
        """Return a fraud probability score (0–100) for a single claim."""
        model = self.load_active_model()
        if not model:
            return {"error": "No active model available", "risk_score": 0.0}

        input_df = pd.DataFrame([self._build_feature_row(claim)])

        try:
            probability = model.predict_proba(input_df)[0][1]
            return {
                "risk_score": round(probability * 100.0, 2),
                "model_used": "gradient_boosting_v2",
                "probability": round(float(probability), 6),
                "features": input_df.to_dict(orient="records")[0],
            }
        except Exception as exc:
            print(f"[MLService] Prediction error: {exc}")
            return {"error": str(exc), "risk_score": 0.0}
