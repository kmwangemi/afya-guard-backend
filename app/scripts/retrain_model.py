"""
SHA Fraud Detection — Weekly Retraining Script
===============================================

Reads all FraudCase outcomes from the database, builds a labelled training
dataset from the associated ClaimFeature rows, retrains the XGBoost classifier,
evaluates it, saves the new .joblib artifacts, registers a new ModelVersion
record in the DB, and optionally auto-deploys it if performance improves.

Usage (run manually or via cron/Celery beat):
    python -m scripts.retrain_model

    # Or with explicit overrides:
    python -m scripts.retrain_model --min-samples 100 --auto-deploy

Cron example (every Sunday at 02:00):
    0 2 * * 0 cd /app && python -m scripts.retrain_model >> /var/log/retrain.log 2>&1

Environment variables read from .env / shell:
    DATABASE_URL        — PostgreSQL connection string
    ML_MODEL_DIR        — directory to save .joblib artifacts (default: models/)
    RETRAIN_MIN_SAMPLES — minimum confirmed fraud cases needed (default: 50)
"""

import argparse
import logging
import math
import sys
from datetime import UTC, datetime, date
from pathlib import Path
from typing import Optional

# ── Make sure the app package is on sys.path when running as a script ─────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    classification_report,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, joinedload
from xgboost import XGBClassifier

from app.core.config import settings
from app.models.enums_model import CaseStatus, ModelType
from app.models.fraud_case_model import FraudCase
from app.models.model_version_model import ModelVersion
from app.models.claim_model import Claim
from app.models.claim_feature_model import ClaimFeature

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("retrain")

# ── Feature columns (must match fraud_service._features_to_dataframe) ─────────
FEATURE_COLUMNS = [
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
    # derived
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


# ══════════════════════════════════════════════════════════════════════════════
# 1. DATA EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════


def extract_training_data(db: Session) -> pd.DataFrame:
    """
    Pull every FraudCase that has been closed with a definitive outcome
    (CONFIRMED_FRAUD or CLEARED) and join it to its ClaimFeature row.

    Returns a DataFrame with FEATURE_COLUMNS + 'label' (1=fraud, 0=legit).
    """
    logger.info("Extracting labelled cases from database …")

    # Only use cases with a final verdict — OPEN / UNDER_REVIEW are excluded
    # because their true label is unknown and would add noise.
    stmt = (
        select(FraudCase)
        .where(FraudCase.status.in_([CaseStatus.CONFIRMED_FRAUD, CaseStatus.CLEARED]))
        .options(
            joinedload(FraudCase.claim).joinedload(Claim.features),
            joinedload(FraudCase.claim).joinedload(Claim.provider),
        )
    )
    cases = db.execute(stmt).scalars().all()
    logger.info(
        f"Found {len(cases)} closed cases "
        f"({sum(1 for c in cases if c.status == CaseStatus.CONFIRMED_FRAUD)} fraud, "
        f"{sum(1 for c in cases if c.status == CaseStatus.CLEARED)} cleared)"
    )

    rows = []
    skipped = 0
    for case in cases:
        claim = case.claim
        if claim is None or claim.features is None:
            skipped += 1
            continue

        features: ClaimFeature = claim.features
        provider = claim.provider

        # ── Replicate the same feature engineering as fraud_service ───────────
        amount = float(claim.total_claim_amount or 0)
        los = float(features.length_of_stay or 0)
        svc = int(features.service_count or 1)

        claim_type_enc = 0 if str(claim.claim_type or "").upper() == "INPATIENT" else 1
        county = str(getattr(provider, "county", "") or "Nairobi")
        county_enc = hash(county) % 10
        facility_level = _parse_facility_level(provider)

        submitted_hour = _extract_hour(claim)
        is_off_hours = int(submitted_hour >= 23 or submitted_hour <= 5)
        eligibility_checked = getattr(features, "eligibility_checked", True)

        row = {
            # ClaimFeature columns
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
            "service_count": svc,
            "has_lab_without_diagnosis": int(bool(features.has_lab_without_diagnosis)),
            "has_surgery_without_theatre": int(
                bool(features.has_surgery_without_theatre)
            ),
            # Derived features
            "claim_type_enc": claim_type_enc,
            "county_enc": county_enc,
            "facility_level": facility_level,
            "log_amount": math.log1p(amount),
            "amount_per_service": round(amount / max(svc, 1), 2),
            "amount_per_day": round(amount / max(los, 1), 2),
            "submitted_hour": submitted_hour,
            "is_off_hours": is_off_hours,
            "no_eligibility_check": int(not bool(eligibility_checked)),
            "high_service_count": int(svc > 8),
            "level_amount_mismatch": int(facility_level <= 2 and amount > 10_000),
            # Label
            "label": 1 if case.status == CaseStatus.CONFIRMED_FRAUD else 0,
        }
        rows.append(row)

    if skipped:
        logger.warning(f"Skipped {skipped} cases with missing claim/feature data")

    df = pd.DataFrame(rows)
    logger.info(
        f"Dataset shape: {df.shape}  |  " f"Fraud rate: {df['label'].mean():.1%}"
    )
    return df


def _parse_facility_level(provider) -> int:
    """Mirror of app.utils.provider_utils.parse_facility_level."""
    if provider is None:
        return 4
    try:
        from app.utils.provider_utils import parse_facility_level

        return parse_facility_level(provider)
    except Exception:
        return 4


def _extract_hour(claim) -> int:
    """Get the submission hour from claim.submitted_at."""
    submitted_at = getattr(claim, "submitted_at", None)
    if submitted_at is None:
        return 0
    try:
        return submitted_at.hour
    except AttributeError:
        return 0


# ══════════════════════════════════════════════════════════════════════════════
# 2. TRAINING
# ══════════════════════════════════════════════════════════════════════════════


def train(df: pd.DataFrame) -> tuple[XGBClassifier, dict]:
    """
    Train an XGBoost classifier on the labelled dataset.

    Uses scale_pos_weight to handle class imbalance — in a fraud dataset
    there will always be far more legitimate claims than fraudulent ones.
    The model is trained on 80% of the data and evaluated on a held-out 20%.

    Returns (trained_model, metrics_dict).
    """
    X = df[FEATURE_COLUMNS].values
    y = df["label"].values

    # Class weight to handle imbalance: n_negative / n_positive
    n_neg = int((y == 0).sum())
    n_pos = int((y == 1).sum())
    scale = round(n_neg / max(n_pos, 1), 2)
    logger.info(
        f"Class balance — legit: {n_neg}, fraud: {n_pos}, scale_pos_weight: {scale}"
    )

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    model = XGBClassifier(
        n_estimators=300,
        max_depth=6,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=scale,
        use_label_encoder=False,
        eval_metric="logloss",
        random_state=42,
        n_jobs=-1,
    )

    model.fit(
        X_train,
        y_train,
        eval_set=[(X_test, y_test)],
        verbose=False,
    )

    # ── Evaluation ─────────────────────────────────────────────────────────
    y_prob = model.predict_proba(X_test)[:, 1]
    y_pred = (y_prob >= 0.5).astype(int)

    metrics = {
        "auc_roc": round(float(roc_auc_score(y_test, y_prob)), 4),
        "auc_pr": round(float(average_precision_score(y_test, y_prob)), 4),
        "precision": round(float(precision_score(y_test, y_pred, zero_division=0)), 4),
        "recall": round(float(recall_score(y_test, y_pred, zero_division=0)), 4),
        "f1": round(float(f1_score(y_test, y_pred, zero_division=0)), 4),
        "n_train": int(len(X_train)),
        "n_test": int(len(X_test)),
        "n_fraud": n_pos,
        "n_legit": n_neg,
    }

    logger.info(
        f"Evaluation — AUC-ROC: {metrics['auc_roc']:.3f}  "
        f"Precision: {metrics['precision']:.3f}  "
        f"Recall: {metrics['recall']:.3f}  "
        f"F1: {metrics['f1']:.3f}"
    )
    logger.info(
        "\n" + classification_report(y_test, y_pred, target_names=["legit", "fraud"])
    )

    return model, metrics


# ══════════════════════════════════════════════════════════════════════════════
# 3. ARTIFACT SAVING
# ══════════════════════════════════════════════════════════════════════════════


def save_artifacts(
    model: XGBClassifier, model_dir: Path, version_tag: str
) -> tuple[Path, Path]:
    """
    Save model and feature list to versioned .joblib files.

    Also writes to the canonical fraud_xgboost.joblib / feature_list.joblib
    filenames so that load_ml_artifacts() always finds the latest model.

    Returns (model_path, feature_path) — the versioned paths.
    """
    model_dir.mkdir(parents=True, exist_ok=True)

    # Versioned paths (archived, never overwritten)
    model_path = model_dir / f"fraud_xgboost_{version_tag}.joblib"
    feature_path = model_dir / f"feature_list_{version_tag}.joblib"

    joblib.dump(model, model_path)
    joblib.dump(FEATURE_COLUMNS, feature_path)

    # Canonical paths (overwritten — always points at the latest trained model)
    joblib.dump(model, model_dir / "fraud_xgboost.joblib")
    joblib.dump(FEATURE_COLUMNS, model_dir / "feature_list.joblib")

    logger.info(f"Artifacts saved: {model_path}")
    return model_path, feature_path


# ══════════════════════════════════════════════════════════════════════════════
# 4. DB REGISTRATION
# ══════════════════════════════════════════════════════════════════════════════


def register_in_db(
    db: Session,
    version_name: str,
    model_path: Path,
    metrics: dict,
    training_df: pd.DataFrame,
) -> ModelVersion:
    """
    Write a new ModelVersion record to the database.
    Does NOT set is_deployed — that is a separate admin decision via the UI
    (or auto-deploy if --auto-deploy flag is passed).
    """
    today = date.today()

    version = ModelVersion(
        version_name=version_name,
        model_type=ModelType.XGBOOST,
        description=(
            f"Auto-retrained from {metrics['n_fraud']} confirmed fraud cases "
            f"and {metrics['n_legit']} cleared cases. "
            f"Training date: {today.isoformat()}."
        ),
        training_start=today,
        training_end=today,
        training_sample_size=metrics["n_train"] + metrics["n_test"],
        model_artifact_path=str(model_path),
        performance_metrics={
            "auc_roc": metrics["auc_roc"],
            "auc_pr": metrics["auc_pr"],
            "precision": metrics["precision"],
            "recall": metrics["recall"],
            "f1": metrics["f1"],
        },
        feature_names=FEATURE_COLUMNS,
        is_deployed=False,
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    logger.info(
        f"ModelVersion '{version_name}' registered in database (id={version.id})"
    )
    return version


# ══════════════════════════════════════════════════════════════════════════════
# 5. AUTO-DEPLOY
# ══════════════════════════════════════════════════════════════════════════════


def auto_deploy_if_better(db: Session, new_version: ModelVersion) -> bool:
    """
    Compare the new model's AUC-ROC against the currently deployed model.
    Deploy the new one only if it is strictly better.

    Returns True if auto-deploy happened.
    """
    current = db.query(ModelVersion).filter(ModelVersion.is_deployed == True).first()

    new_auc = (new_version.performance_metrics or {}).get("auc_roc", 0)
    cur_auc = (current.performance_metrics or {}).get("auc_roc", 0) if current else 0

    if new_auc <= cur_auc:
        logger.info(
            f"Auto-deploy skipped: new AUC-ROC {new_auc:.4f} ≤ "
            f"current {cur_auc:.4f} ('{getattr(current, 'version_name', 'none')}')"
        )
        return False

    logger.info(
        f"New model is better ({new_auc:.4f} > {cur_auc:.4f}). Auto-deploying …"
    )

    # Deactivate existing
    if current:
        current.is_deployed = False

    # Activate new
    new_version.is_deployed = True
    new_version.deployed_at = datetime.now(UTC)
    db.commit()

    # Hot-swap in-memory model so live scoring immediately uses the new artifact
    if new_version.model_artifact_path:
        try:
            from app.services.fraud_service import reload_ml_artifacts

            reload_ml_artifacts(artifact_path=new_version.model_artifact_path)
            logger.info("In-memory model hot-swapped successfully")
        except Exception as exc:
            logger.warning(f"Hot-swap after auto-deploy failed: {exc}")

    return True


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════


def main(min_samples: Optional[int] = None, auto_deploy: bool = False) -> None:
    min_samples = min_samples or settings.RETRAIN_MIN_SAMPLES

    engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)

    with Session(engine) as db:
        # ── Step 1: Extract ──────────────────────────────────────────────────
        df = extract_training_data(db)

        fraud_count = int(df["label"].sum())
        if fraud_count < min_samples:
            logger.warning(
                f"Only {fraud_count} confirmed fraud cases found "
                f"(minimum required: {min_samples}). "
                f"Retraining skipped — collect more confirmed outcomes first."
            )
            return

        # ── Step 2: Train ────────────────────────────────────────────────────
        model, metrics = train(df)

        # ── Step 3: Save artifacts ───────────────────────────────────────────
        version_tag = datetime.now(UTC).strftime("%Y%m%d_%H%M")
        version_name = f"xgboost-v-{version_tag}"
        model_path, _ = save_artifacts(model, settings.MODEL_DIR, version_tag)

        # ── Step 4: Register in DB ───────────────────────────────────────────
        new_version = register_in_db(db, version_name, model_path, metrics, df)

        # ── Step 5: Optionally auto-deploy ───────────────────────────────────
        if auto_deploy:
            deployed = auto_deploy_if_better(db, new_version)
            if not deployed:
                logger.info(
                    f"New model registered as '{version_name}'. "
                    f"Deploy it manually from the ML Engine page."
                )
        else:
            logger.info(
                f"Retraining complete. New model: '{version_name}'. "
                f"Review it on the ML Engine page and deploy when ready."
            )

    logger.info("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Retrain XGBoost fraud model from confirmed cases"
    )
    parser.add_argument(
        "--min-samples",
        type=int,
        default=None,
        help="Minimum confirmed fraud cases required (overrides RETRAIN_MIN_SAMPLES env var)",
    )
    parser.add_argument(
        "--auto-deploy",
        action="store_true",
        help="Automatically deploy the new model if its AUC-ROC exceeds the current deployed model",
    )
    args = parser.parse_args()
    main(min_samples=args.min_samples, auto_deploy=args.auto_deploy)
