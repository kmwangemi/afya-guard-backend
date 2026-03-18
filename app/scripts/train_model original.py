"""
scripts/train_model.py
───────────────────────
Trains XGBoost on the clean 23-feature dataset.
Run after: python scripts/generate_dataset.py
"""

import os

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

df = pd.read_csv("data/sha_claims_dataset.csv")
print(f"Loaded {len(df)} rows — fraud rate: {df['is_fraud'].mean()*100:.1f}%")

# ── Feature definition (must match ALL_FEATURES in generate_dataset.py) ───────

FEATURES_GROUP_A = [
    "provider_avg_cost_90d",
    "provider_cost_zscore",
    "member_visits_30d",
    "member_visits_7d",
    "member_unique_providers_30d",
    "duplicate_within_7d",
    "length_of_stay",
    "weekend_submission",  # ← single weekend flag (not duplicated)
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
    "submitted_hour",  # ← single time feature (no weekday duplicate)
    "is_off_hours",
    "no_eligibility_check",
    "high_service_count",
    "level_amount_mismatch",
]

ALL_FEATURES = FEATURES_GROUP_A + FEATURES_GROUP_B
print(
    f"Total features: {len(ALL_FEATURES)} (A: {len(FEATURES_GROUP_A)}, B: {len(FEATURES_GROUP_B)})"
)

X = df[ALL_FEATURES].fillna(0)
y = df["is_fraud"]

X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, stratify=y, random_state=42
)

smote = SMOTE(random_state=42, sampling_strategy=0.5)
X_train_bal, y_train_bal = smote.fit_resample(X_train, y_train)
print(f"After SMOTE — fraud: {y_train_bal.sum()} | legit: {(y_train_bal==0).sum()}")

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

y_pred = model.predict(X_test)
y_pred_prob = model.predict_proba(X_test)[:, 1]

print("\n── Classification Report ─────────────────────────────────")
print(classification_report(y_test, y_pred, target_names=["Legitimate", "Fraud"]))

cm = confusion_matrix(y_test, y_pred)
print("── Confusion Matrix ──────────────────────────────────────")
print(f"  True  Legit : {cm[0][0]:>6}  |  False Fraud: {cm[0][1]:>6}")
print(f"  False Legit : {cm[1][0]:>6}  |  True  Fraud: {cm[1][1]:>6}")
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

print("\n── Feature Importance ────────────────────────────────────")
imp = pd.Series(model.feature_importances_, index=ALL_FEATURES).sort_values(
    ascending=False
)
print(imp.to_string())
print(f"\nGroup A total: {imp[FEATURES_GROUP_A].sum():.4f}")
print(f"Group B total: {imp[FEATURES_GROUP_B].sum():.4f}")

joblib.dump(model, "ml_models/fraud_xgboost.joblib")
joblib.dump(ALL_FEATURES, "ml_models/feature_list.joblib")
print("\nSaved → ml_models/fraud_xgboost.joblib")
print("Saved → ml_models/feature_list.joblib")

# Train the model
# python app/scripts/train_model.py
