"""
scripts/train_model.py
───────────────────────
Trains XGBoost on the 23-feature SHA claims dataset.
Run after: python scripts/generate_dataset.py

Usage:
    python scripts/train_model.py

Inputs:
    data/sha_claims_dataset.csv
    data/county_encoder.json

Outputs:
    ml_models/fraud_xgboost.joblib
    ml_models/feature_list.joblib
    ml_models/county_encoder.json   ← copied here for fraud_service.py to load
"""

import json
import os
import shutil

import joblib
import pandas as pd
from imblearn.over_sampling import SMOTE
from sklearn.metrics import (
    average_precision_score,
    classification_report,
    confusion_matrix,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
from xgboost import XGBClassifier

os.makedirs("ml_models", exist_ok=True)

# ── Load dataset ──────────────────────────────────────────────────────────────

CSV_PATH = "data/sha_claims_dataset.csv"
ENCODER_PATH = "data/county_encoder.json"

if not os.path.exists(CSV_PATH):
    raise FileNotFoundError(f"{CSV_PATH} not found. Run generate_dataset.py first.")
if not os.path.exists(ENCODER_PATH):
    raise FileNotFoundError(
        f"{ENCODER_PATH} not found. Run generate_dataset.py first.\n"
        f"This file is required so the serving code uses the same county "
        f"encoding as the trained model."
    )

df = pd.read_csv(CSV_PATH)
print(f"Loaded {len(df)} rows — fraud rate: {df['is_fraud'].mean() * 100:.1f}%")

# ── Feature definition ────────────────────────────────────────────────────────
# Must match ALL_FEATURES in generate_dataset.py exactly — both list and order.

FEATURES_GROUP_A = [
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
]

FEATURES_GROUP_B = [
    "claim_type_enc",
    "county_enc",
    "facility_level",
    "log_amount",
    "amount_per_service",
    "amount_per_day",
    "submitted_hour",
    "is_off_hours",
    "no_eligibility_check",
    "high_service_count",
    "level_amount_mismatch",
]

ALL_FEATURES = FEATURES_GROUP_A + FEATURES_GROUP_B
print(
    f"Total features: {len(ALL_FEATURES)} "
    f"(A: {len(FEATURES_GROUP_A)}, B: {len(FEATURES_GROUP_B)})"
)

# Verify every expected feature exists in the CSV
missing_cols = [f for f in ALL_FEATURES if f not in df.columns]
if missing_cols:
    raise ValueError(
        f"Dataset is missing columns: {missing_cols}\n"
        f"Re-run generate_dataset.py to regenerate the dataset."
    )

# ── Split ─────────────────────────────────────────────────────────────────────

X = df[ALL_FEATURES].fillna(0)
y = df["is_fraud"]

X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, stratify=y, random_state=42
)

# ── SMOTE oversampling ────────────────────────────────────────────────────────

smote = SMOTE(random_state=42, sampling_strategy=0.5)
X_train_bal, y_train_bal = smote.fit_resample(X_train, y_train)
print(
    f"After SMOTE — fraud: {y_train_bal.sum()} | " f"legit: {(y_train_bal == 0).sum()}"
)

# ── Train ─────────────────────────────────────────────────────────────────────

model = XGBClassifier(
    n_estimators=400,
    max_depth=6,
    learning_rate=0.05,
    subsample=0.8,
    colsample_bytree=0.8,
    min_child_weight=3,
    gamma=0.1,
    reg_alpha=0.1,
    reg_lambda=1.0,
    eval_metric="aucpr",
    random_state=42,
    n_jobs=-1,
)
model.fit(X_train_bal, y_train_bal, eval_set=[(X_test, y_test)], verbose=50)

# ── Evaluate ──────────────────────────────────────────────────────────────────

y_pred = model.predict(X_test)
y_pred_prob = model.predict_proba(X_test)[:, 1]

print("\n── Classification report ─────────────────────────────────")
print(classification_report(y_test, y_pred, target_names=["Legitimate", "Fraud"]))

cm = confusion_matrix(y_test, y_pred)
print("── Confusion matrix ──────────────────────────────────────")
print(f"  True  Legit : {cm[0][0]:>6}  |  False Fraud : {cm[0][1]:>6}")
print(f"  False Legit : {cm[1][0]:>6}  |  True  Fraud : {cm[1][1]:>6}")
print(f"\nROC-AUC : {roc_auc_score(y_test, y_pred_prob):.4f}")
print(f"PR-AUC  : {average_precision_score(y_test, y_pred_prob):.4f}")

cv_scores = cross_val_score(
    model,
    X,
    y,
    cv=StratifiedKFold(n_splits=5, shuffle=True, random_state=42),
    scoring="roc_auc",
    n_jobs=-1,
)
print(f"5-Fold CV ROC-AUC: {cv_scores.mean():.4f} ± {cv_scores.std():.4f}")

print("\n── Feature importance ────────────────────────────────────")
imp = pd.Series(model.feature_importances_, index=ALL_FEATURES).sort_values(
    ascending=False
)
print(imp.to_string())
print(f"\nGroup A total : {imp[FEATURES_GROUP_A].sum():.4f}")
print(f"Group B total : {imp[FEATURES_GROUP_B].sum():.4f}")

# ── Save artefacts ────────────────────────────────────────────────────────────

MODEL_PATH = "ml_models/fraud_xgboost.joblib"
FEATURE_LIST_PATH = "ml_models/feature_list.joblib"
MODEL_ENCODER_PATH = "ml_models/county_encoder.json"

joblib.dump(model, MODEL_PATH)
joblib.dump(ALL_FEATURES, FEATURE_LIST_PATH)

# Copy county_encoder.json into ml_models/ so fraud_service.py has a single
# directory to point MODEL_DIR at and finds all three artefacts together.
shutil.copy(ENCODER_PATH, MODEL_ENCODER_PATH)

# ── Assertion: feature list must match what was just saved ────────────────────
loaded_features = joblib.load(FEATURE_LIST_PATH)
assert ALL_FEATURES == loaded_features, (
    f"Feature list drift between save and load:\n"
    f"  missing : {set(ALL_FEATURES) - set(loaded_features)}\n"
    f"  extra   : {set(loaded_features) - set(ALL_FEATURES)}"
)

print(f"\nSaved → {MODEL_PATH}")
print(f"Saved → {FEATURE_LIST_PATH}")
print(f"Saved → {MODEL_ENCODER_PATH}")
print("Feature list assertion passed.")

# ── Serving-side reminder ─────────────────────────────────────────────────────
print(
    """
── Update fraud_service.py ───────────────────────────────
In load_ml_artifacts(), add after loading the model:

    encoder_path = model_dir / "county_encoder.json"
    if encoder_path.exists():
        with open(encoder_path) as f:
            _county_encoder = json.load(f)
    else:
        logger.warning("county_encoder.json missing — county_enc will be wrong")

In _features_to_dataframe(), replace:
    county_enc = hash(county) % 10
with:
    county_enc = _county_encoder.get(county, 8)
──────────────────────────────────────────────────────────
"""
)

# Train the model
# python app/scripts/train_model.py
