"""
scripts/train_upcoding_model.py
────────────────────────────────
Trains a Random Forest classifier to detect upcoding fraud.

Features (15):
  1.  price_overrun_ratio        — unit_price / reference_price (max across services)
  2.  incompatible_service_count — services not matching any diagnosis
  3.  inpatient_code_outpatient  — inpatient-only codes on outpatient claim (0/1)
  4.  mutual_exclusion_violation — mutually exclusive codes billed together (0/1)
  5.  high_value_zero_los        — high-value service + 0-day stay (0/1)
  6.  facility_level_mismatch    — service requires higher facility than submitted
  7.  diagnosis_cost_zscore      — (claim_amount - diag_mean) / diag_std
  8.  peer_cost_zscore           — (claim_amount - peer_mean) / peer_std
  9.  service_count              — number of line items
  10. max_quantity                — highest quantity of any single service
  11. amount_per_service         — total_amount / service_count
  12. log_amount                 — log(1 + total_amount)
  13. claim_type_enc             — INPATIENT=0, OUTPATIENT=1
  14. facility_level             — 1–6
  15. expected_service_ratio     — matched_expected / total_services (0–1)

Usage:
    python scripts/train_upcoding_model.py
Outputs:
    ml_models/upcoding_rf.joblib
    ml_models/upcoding_features.joblib
"""

import os
import random

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    average_precision_score,
    classification_report,
    confusion_matrix,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split

random.seed(42)
np.random.seed(42)

os.makedirs("ml_models", exist_ok=True)

# ── Feature list (single source of truth) ────────────────────────────────────

UPCODING_FEATURES = [
    "price_overrun_ratio",
    "incompatible_service_count",
    "inpatient_code_outpatient",
    "mutual_exclusion_violation",
    "high_value_zero_los",
    "facility_level_mismatch",
    "diagnosis_cost_zscore",
    "peer_cost_zscore",
    "service_count",
    "max_quantity",
    "amount_per_service",
    "log_amount",
    "claim_type_enc",
    "facility_level",
    "expected_service_ratio",
]

# ── Synthetic data generation ─────────────────────────────────────────────────


def make_legit_claim() -> dict:
    facility_level = random.randint(1, 6)
    claim_type_enc = random.randint(0, 1)  # 0=inpatient, 1=outpatient
    service_count = random.randint(1, 6)
    amount = random.uniform(1_000, 50_000)

    return {
        "price_overrun_ratio": random.uniform(0.5, 1.8),
        "incompatible_service_count": random.choices([0, 1], weights=[0.95, 0.05])[0],
        "inpatient_code_outpatient": 0,
        "mutual_exclusion_violation": 0,
        "high_value_zero_los": 0,
        "facility_level_mismatch": 0,
        "diagnosis_cost_zscore": random.uniform(-1.5, 1.5),
        "peer_cost_zscore": random.uniform(-1.5, 1.5),
        "service_count": service_count,
        "max_quantity": random.randint(1, 5),
        "amount_per_service": round(amount / max(service_count, 1), 2),
        "log_amount": round(float(np.log1p(amount)), 6),
        "claim_type_enc": claim_type_enc,
        "facility_level": facility_level,
        "expected_service_ratio": random.uniform(0.6, 1.0),
        "is_upcoding": 0,
        "fraud_type": None,
    }


def inject_price_overrun(claim: dict) -> dict:
    claim["price_overrun_ratio"] = random.uniform(2.5, 8.0)
    claim["diagnosis_cost_zscore"] = random.uniform(3.0, 7.0)
    claim["peer_cost_zscore"] = random.uniform(3.0, 6.0)
    claim["is_upcoding"] = 1
    claim["fraud_type"] = "price_overrun"
    return claim


def inject_inpatient_on_outpatient(claim: dict) -> dict:
    claim["inpatient_code_outpatient"] = 1
    claim["claim_type_enc"] = 1  # outpatient
    claim["high_value_zero_los"] = 1
    claim["diagnosis_cost_zscore"] = random.uniform(2.0, 5.0)
    claim["is_upcoding"] = 1
    claim["fraud_type"] = "inpatient_on_outpatient"
    return claim


def inject_mutual_exclusion(claim: dict) -> dict:
    claim["mutual_exclusion_violation"] = 1
    claim["service_count"] = random.randint(4, 8)
    claim["price_overrun_ratio"] = random.uniform(1.8, 3.5)
    claim["is_upcoding"] = 1
    claim["fraud_type"] = "mutual_exclusion"
    return claim


def inject_incompatible_services(claim: dict) -> dict:
    claim["incompatible_service_count"] = random.randint(2, 4)
    claim["diagnosis_cost_zscore"] = random.uniform(3.0, 8.0)
    claim["peer_cost_zscore"] = random.uniform(2.5, 6.0)
    claim["is_upcoding"] = 1
    claim["fraud_type"] = "incompatible_service"
    return claim


def inject_facility_mismatch(claim: dict) -> dict:
    claim["facility_level_mismatch"] = 1
    claim["facility_level"] = random.randint(1, 2)
    claim["price_overrun_ratio"] = random.uniform(2.0, 5.0)
    claim["peer_cost_zscore"] = random.uniform(3.0, 7.0)
    claim["is_upcoding"] = 1
    claim["fraud_type"] = "facility_mismatch"
    return claim


def inject_high_value_zero_los(claim: dict) -> dict:
    amount = random.uniform(60_000, 200_000)
    claim["high_value_zero_los"] = 1
    claim["log_amount"] = round(float(np.log1p(amount)), 6)
    claim["claim_type_enc"] = 1
    claim["diagnosis_cost_zscore"] = random.uniform(2.5, 6.0)
    claim["is_upcoding"] = 1
    claim["fraud_type"] = "high_value_zero_los"
    return claim


def generate_dataset(n_legit: int = 8_000, fraud_ratio: float = 0.20) -> pd.DataFrame:
    print(f"Generating {n_legit} legitimate claims...")
    claims = [make_legit_claim() for _ in range(n_legit)]

    n_fraud = int(n_legit * fraud_ratio / (1 - fraud_ratio))
    print(f"Injecting {n_fraud} upcoding fraud samples ({fraud_ratio*100:.0f}%)...")

    injectors = [
        inject_price_overrun,
        inject_inpatient_on_outpatient,
        inject_mutual_exclusion,
        inject_incompatible_services,
        inject_facility_mismatch,
        inject_high_value_zero_los,
    ]

    fraud_claims = []
    for _ in range(n_fraud):
        base = make_legit_claim()
        base = random.choice(injectors)(base)
        fraud_claims.append(base)

    all_claims = claims + fraud_claims
    random.shuffle(all_claims)
    df = pd.DataFrame(all_claims)

    print(f"\n── Dataset Summary ───────────────────────────────────────")
    print(f"  Total rows  : {len(df)}")
    print(f"  Legitimate  : {(df['is_upcoding']==0).sum()}")
    print(
        f"  Fraud       : {df['is_upcoding'].sum()} ({df['is_upcoding'].mean()*100:.1f}%)"
    )
    print(f"\n── Fraud type breakdown ──────────────────────────────────")
    print(df[df["is_upcoding"] == 1]["fraud_type"].value_counts().to_string())

    return df


# ── Train ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    df = generate_dataset()
    X = df[UPCODING_FEATURES].fillna(0)
    y = df["is_upcoding"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, stratify=y, random_state=42
    )

    model = RandomForestClassifier(
        n_estimators=300,
        max_depth=12,
        min_samples_split=5,
        min_samples_leaf=2,
        max_features="sqrt",
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    y_pred_prob = model.predict_proba(X_test)[:, 1]

    print("\n── Classification Report ─────────────────────────────────")
    print(classification_report(y_test, y_pred, target_names=["Legit", "Upcoding"]))

    cm = confusion_matrix(y_test, y_pred)
    print("── Confusion Matrix ──────────────────────────────────────")
    print(f"  True  Legit   : {cm[0][0]:>6}  |  False Fraud : {cm[0][1]:>6}")
    print(f"  False Legit   : {cm[1][0]:>6}  |  True  Fraud : {cm[1][1]:>6}")
    print(f"\nROC-AUC : {roc_auc_score(y_test, y_pred_prob):.4f}")
    print(f"PR-AUC  : {average_precision_score(y_test, y_pred_prob):.4f}")

    cv = cross_val_score(
        model,
        X,
        y,
        cv=StratifiedKFold(5, shuffle=True, random_state=42),
        scoring="roc_auc",
        n_jobs=-1,
    )
    print(f"5-Fold CV ROC-AUC: {cv.mean():.4f} ± {cv.std():.4f}")

    print("\n── Feature Importance ────────────────────────────────────")
    imp = pd.Series(model.feature_importances_, index=UPCODING_FEATURES).sort_values(
        ascending=False
    )
    print(imp.to_string())

    joblib.dump(model, "ml_models/upcoding_rf.joblib")
    joblib.dump(UPCODING_FEATURES, "ml_models/upcoding_features.joblib")
    print("\nSaved → ml_models/upcoding_rf.joblib")
    print("Saved → ml_models/upcoding_features.joblib")


# Train the model
# python app/scripts/train_upcoding_model.py
