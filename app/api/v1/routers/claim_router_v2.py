"""
SHA Fraud Detection System — Claims Router
Aligned to SHAClaimExtractor and the Social Health Insurance Act, 2023 claim form.
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks, status
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
import shutil
import uuid
from pathlib import Path

from app.config import settings
from app.database import get_db
from app import schemas, models
from app.services.claim_extractor import sha_claim_extractor, SHAClaimData
from app.services.fraud_analyzer import analyze_claim_background   # top-level import
from app.utils.auth import get_current_user

import pandas as pd

router = APIRouter()

# ---------------------------------------------------------------------------
# Allowed file extensions — single source of truth
# ---------------------------------------------------------------------------
ALLOWED_EXTENSIONS = {".pdf", ".xlsx", ".xls", ".csv", ".docx"}


def _save_upload(file: UploadFile, user_id: int) -> Path:
    """
    Persist an uploaded file to disk with a collision-safe name.
    Path: <UPLOAD_DIR>/<user_id>/<uuid4>_<original_filename>
    Returns the resolved Path.
    """
    upload_dir = Path(settings.UPLOAD_DIR) / str(user_id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    unique_name = f"{uuid.uuid4().hex}_{Path(file.filename).name}"
    dest = upload_dir / unique_name

    try:
        with dest.open("wb") as buf:
            shutil.copyfileobj(file.file, buf)
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not save uploaded file: {exc}",
        )
    return dest


# ---------------------------------------------------------------------------
# POST /upload-and-extract
# Single atomic endpoint: receive file → extract → return structured data.
# The frontend does NOT need to know the server-side file path.
# ---------------------------------------------------------------------------

@router.post("/upload-and-extract", response_model=schemas.ExtractResponse)
async def upload_and_extract(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Upload a SHA claim file and immediately extract structured data.

    Accepts: PDF, Excel (.xlsx/.xls), CSV, DOCX
    Returns: extracted SHAClaimData fields + validation errors (if any).
    The frontend should display validation errors to the user before calling /submit.
    """
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    saved_path = _save_upload(file, current_user.id)

    try:
        claim_data: SHAClaimData = sha_claim_extractor.extract_from_file(str(saved_path))
    except Exception as exc:
        saved_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Extraction failed: {exc}",
        )

    is_valid, validation_errors = sha_claim_extractor.validate(claim_data)

    return {
        "file_path": str(saved_path),           # opaque token the frontend passes to /submit
        "file_size": saved_path.stat().st_size,
        "extracted": claim_data.to_dict(),
        "is_valid": is_valid,
        "validation_errors": validation_errors,
    }


# ---------------------------------------------------------------------------
# POST /submit
# Takes the pre-extracted + user-reviewed data and persists a Claim record.
# ---------------------------------------------------------------------------

@router.post("/submit", response_model=schemas.ClaimResponse)
async def submit_claim(
    claim_data: schemas.ClaimCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Submit a validated SHA claim for fraud analysis and processing.

    The frontend should call this after the user reviews and confirms the
    data returned by /upload-and-extract.
    """
    # ── Resolve provider ──────────────────────────────────────────────────
    provider = (
        db.query(models.Provider)
        .filter(models.Provider.provider_id == claim_data.provider_id)
        .first()
    )
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Provider '{claim_data.provider_id}' not found. "
                "Register the provider before submitting claims."
            ),
        )

    # ── Generate claim number ─────────────────────────────────────────────
    claim_number = sha_claim_extractor.generate_claim_number(claim_data.provider_id)

    # ── Build DB record — fields match SHAClaimData exactly ──────────────
    db_claim = models.Claim(
        claim_number=claim_number,

        # Part I — Provider
        provider_id=provider.id,
        provider_code=claim_data.provider_id,       # stored as provider_id on the form
        provider_name=claim_data.provider_name,

        # Part II — Patient (SHA form has no National ID field; sha_number is primary)
        patient_sha_number=claim_data.sha_number,
        patient_last_name=claim_data.patient_last_name,
        patient_first_name=claim_data.patient_first_name,
        patient_middle_name=claim_data.patient_middle_name,
        patient_residence=claim_data.residence,
        other_insurance=claim_data.other_insurance,
        relationship_to_principal=claim_data.relationship_to_principal,

        # Part III — Visit
        was_referred=claim_data.was_referred,
        referral_provider=claim_data.referral_provider,
        visit_type=claim_data.visit_type,                       # Inpatient|Outpatient|Day-care
        visit_admission_date=claim_data.visit_admission_date,
        op_ip_number=claim_data.op_ip_number,
        new_or_return_visit=claim_data.new_or_return_visit,
        discharge_date=claim_data.discharge_date,
        rendering_physician=claim_data.rendering_physician,     # name + reg no combined
        accommodation_type=claim_data.accommodation_type,

        # Field 9 — Disposition
        patient_disposition=claim_data.patient_disposition,

        # Field 10 — Discharge referral
        discharge_referral_institution=claim_data.discharge_referral_institution,
        discharge_referral_reason=claim_data.discharge_referral_reason,

        # Fields 11 & 12 — Diagnoses
        admission_diagnosis=claim_data.admission_diagnosis,
        discharge_diagnosis=claim_data.discharge_diagnosis,
        icd11_code=claim_data.icd11_code,
        related_procedure=claim_data.related_procedure,
        procedure_date=claim_data.procedure_date,

        # Field 14 — Benefit lines (stored as JSONB array)
        benefit_lines=[line.dict() for line in claim_data.benefit_lines]
        if claim_data.benefit_lines else [],

        # Derived totals (computed by extractor, confirmed by user)
        total_bill_amount=claim_data.total_bill_amount,
        total_claim_amount=claim_data.total_claim_amount,

        # Declaration
        patient_authorised_name=claim_data.patient_authorised_name,
        declaration_date=claim_data.declaration_date,

        # System fields
        submitted_by=current_user.id,
        submission_date=datetime.utcnow(),
        status=models.ClaimStatus.PROCESSING,
    )

    db.add(db_claim)
    db.commit()
    db.refresh(db_claim)

    # ── Schedule fraud analysis ───────────────────────────────────────────
    background_tasks.add_task(analyze_claim_background, db_claim.id)

    return db_claim


# ---------------------------------------------------------------------------
# POST /bulk-upload
# Bulk ingest from CSV / Excel. One row = one claim.
# ---------------------------------------------------------------------------

@router.post("/bulk-upload", response_model=schemas.BulkUploadResponse)
async def bulk_upload_claims(
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks,           # not Optional — must be injected
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Bulk upload claims from a CSV or Excel file.
    Each row must represent one complete claim.

    Required columns (matching SHA form fields):
        provider_id, sha_number, visit_type, visit_admission_date,
        total_claim_amount, total_bill_amount

    Optional but strongly recommended:
        patient_last_name, patient_first_name, discharge_date,
        accommodation_type, icd11_code, patient_disposition
    """
    ext = Path(file.filename).suffix.lower()
    if ext not in {".csv", ".xlsx", ".xls"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bulk upload accepts CSV or Excel files only.",
        )

    try:
        df = pd.read_csv(file.file) if ext == ".csv" else pd.read_excel(file.file)
        df = df.fillna("")
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not parse file: {exc}",
        )

    # ── Validate required columns ─────────────────────────────────────────
    required_cols = {
        "provider_id",
        "sha_number",
        "visit_type",
        "visit_admission_date",
        "total_claim_amount",
        "total_bill_amount",
    }
    missing_cols = required_cols - set(df.columns)
    if missing_cols:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Missing required columns: {sorted(missing_cols)}",
        )

    # ── Validate visit_type values before processing any rows ────────────
    invalid_visit_types = (
        df[~df["visit_type"].str.lower().isin({"inpatient", "outpatient", "day-care", "day care"})]
    )
    if not invalid_visit_types.empty:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid visit_type values in rows: "
                f"{list(invalid_visit_types.index + 2)}. "   # +2: 1-indexed + header row
                "Must be one of: Inpatient, Outpatient, Day-care"
            ),
        )

    claim_ids: List[int] = []
    errors: List[dict] = []

    for idx, row in df.iterrows():
        row_num = int(idx) + 2  # 1-indexed + header row
        try:
            provider = (
                db.query(models.Provider)
                .filter(models.Provider.provider_id == row["provider_id"])
                .first()
            )
            if not provider:
                errors.append({
                    "row": row_num,
                    "error": f"Provider '{row['provider_id']}' not found",
                })
                continue

            claim_number = sha_claim_extractor.generate_claim_number(str(row["provider_id"]))

            db_claim = models.Claim(
                claim_number=claim_number,
                provider_id=provider.id,
                provider_code=str(row["provider_id"]),
                provider_name=str(row.get("provider_name", "")),

                # Patient
                patient_sha_number=str(row["sha_number"]),
                patient_last_name=str(row.get("patient_last_name", "")),
                patient_first_name=str(row.get("patient_first_name", "")),
                patient_middle_name=str(row.get("patient_middle_name", "")),
                patient_residence=str(row.get("residence", "")),
                relationship_to_principal=str(row.get("relationship_to_principal", "")),

                # Visit — visit_type already validated above; preserve actual value
                visit_type=str(row["visit_type"]).strip().title(),
                visit_admission_date=pd.to_datetime(row["visit_admission_date"]),
                discharge_date=(
                    pd.to_datetime(row["discharge_date"])
                    if row.get("discharge_date")
                    else None
                ),
                accommodation_type=str(row.get("accommodation_type", "")),
                op_ip_number=str(row.get("op_ip_number", "")),
                new_or_return_visit=str(row.get("new_or_return_visit", "")),
                rendering_physician=str(row.get("rendering_physician", "")),

                # Referral
                was_referred=(
                    str(row.get("was_referred", "")).lower() in {"yes", "true", "1"}
                ),
                referral_provider=str(row.get("referral_provider", "")),

                # Disposition
                patient_disposition=str(row.get("patient_disposition", "")),

                # Diagnoses
                admission_diagnosis=str(row.get("admission_diagnosis", "")),
                discharge_diagnosis=str(row.get("discharge_diagnosis", "")),
                icd11_code=str(row.get("icd11_code", "")),
                related_procedure=str(row.get("related_procedure", "")),

                # Amounts — no single scalar; amounts live on benefit lines in the form,
                # but for bulk CSV we accept pre-aggregated totals
                total_bill_amount=float(row["total_bill_amount"]),
                total_claim_amount=float(row["total_claim_amount"]),

                submitted_by=current_user.id,
                submission_date=datetime.utcnow(),
                status=models.ClaimStatus.PROCESSING,
            )

            db.add(db_claim)
            db.commit()
            db.refresh(db_claim)

            claim_ids.append(db_claim.id)
            background_tasks.add_task(analyze_claim_background, db_claim.id)

        except Exception as exc:
            db.rollback()
            errors.append({"row": row_num, "error": str(exc)})

    return {
        "uploaded_count": len(claim_ids),
        "claim_ids": claim_ids,
        "failed_count": len(errors),
        "errors": errors,
        "message": (
            f"Successfully submitted {len(claim_ids)} claims. "
            f"{len(errors)} failed."
        ),
    }


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

@router.get("/", response_model=List[schemas.ClaimResponse])
async def list_claims(
    claim_status: Optional[models.ClaimStatus] = None,  # renamed: 'status' shadows builtin
    is_flagged: Optional[bool] = None,
    provider_id: Optional[str] = None,                  # matches SHA form field name
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    List claims with optional filters. Results are paginated.
    `claim_status` replaces the original `status` parameter name
    (which shadows Python's built-in `status`).
    """
    query = db.query(models.Claim)

    if claim_status:
        query = query.filter(models.Claim.status == claim_status)
    if is_flagged is not None:
        query = query.filter(models.Claim.is_flagged == is_flagged)
    if provider_id:
        query = query.filter(models.Claim.provider_code == provider_id)
    if date_from:
        query = query.filter(models.Claim.visit_admission_date >= date_from)
    if date_to:
        query = query.filter(models.Claim.visit_admission_date <= date_to)

    claims = (
        query.order_by(models.Claim.submission_date.desc())
        .offset(offset)
        .limit(min(limit, 200))          # hard cap prevents runaway queries
        .all()
    )
    return claims


# ---------------------------------------------------------------------------
# GET /{claim_id}
# ---------------------------------------------------------------------------

@router.get("/{claim_id}", response_model=schemas.ClaimDetailResponse)
async def get_claim(
    claim_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Retrieve full details for a single claim."""
    claim = db.query(models.Claim).filter(models.Claim.id == claim_id).first()
    if not claim:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Claim not found")
    return claim


# ---------------------------------------------------------------------------
# GET /{claim_id}/risk-score
# ---------------------------------------------------------------------------

@router.get("/{claim_id}/risk-score", response_model=schemas.RiskScoreResponse)
async def get_claim_risk_score(
    claim_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Return the fraud risk score and flag details for a claim.
    If fraud analysis has not yet completed, `risk_score` will be None.
    """
    claim = db.query(models.Claim).filter(models.Claim.id == claim_id).first()
    if not claim:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Claim not found")

    return {
        "claim_id": claim.id,
        "claim_number": claim.claim_number,
        "risk_score": claim.risk_score,
        "is_flagged": claim.is_flagged,
        "status": claim.status,
        "fraud_flags": claim.fraud_flags or [],
        "analysis_completed_at": claim.analysis_completed_at,
    }


# ---------------------------------------------------------------------------
# PUT /{claim_id}/status
# ---------------------------------------------------------------------------

@router.put("/{claim_id}/status", response_model=schemas.ClaimResponse)
async def update_claim_status(
    claim_id: int,
    update_data: schemas.ClaimUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Update a claim's status, add processing notes, approve/reject.
    Only authorised reviewers should call this endpoint
    (enforce in get_current_user or add a role check here).
    """
    claim = db.query(models.Claim).filter(models.Claim.id == claim_id).first()
    if not claim:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Claim not found")

    if update_data.status:
        claim.status = update_data.status

    if update_data.processing_notes:
        claim.processing_notes = update_data.processing_notes

    if update_data.approved_amount is not None:
        if update_data.approved_amount > claim.total_claim_amount:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="approved_amount cannot exceed the submitted total_claim_amount",
            )
        claim.approved_amount = update_data.approved_amount
        claim.approved_by = current_user.id
        claim.approved_at = datetime.utcnow()

    if update_data.rejection_reason:
        claim.rejection_reason = update_data.rejection_reason

    db.commit()
    db.refresh(claim)
    return claim