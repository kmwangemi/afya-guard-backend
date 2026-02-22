"""
SHA Fraud Detection System — Claims Router
File storage: Cloudinary (replaces local disk storage)
All other flow remains identical to the previous version.
"""

import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import cloudinary
import cloudinary.uploader
import httpx
import pandas as pd
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.claim_model import Claim
from app.models.enums_model import ClaimStatus
from app.models.provider_model import Provider
from app.models.user_model import User
from app.schemas.claim_schema import (
    BulkUploadResponse,
    ClaimCreate,
    ClaimDetailResponse,
    ClaimResponse,
    ClaimUpdate,
    ExtractResponse,
    RiskScoreResponse,
)
from app.services.claim_extractor import SHAClaimData, sha_claim_extractor
from app.services.fraud_analyzer import analyze_claim_background

router = APIRouter()

# ---------------------------------------------------------------------------
# Cloudinary configuration
# Expects these in your settings / environment variables:
#   CLOUDINARY_CLOUD_NAME
#   CLOUDINARY_API_KEY
#   CLOUDINARY_API_SECRET
# ---------------------------------------------------------------------------

cloudinary.config(
    cloud_name=settings.cloudinary_cloud_name,
    api_key=settings.cloudinary_api_key,
    api_secret=settings.cloudinary_api_secret,
    secure=True,
)

ALLOWED_EXTENSIONS = {".pdf", ".xlsx", ".xls", ".csv", ".docx"}

# Cloudinary resource type per extension.
# Non-image files must use "raw".
CLOUDINARY_RESOURCE_TYPES = {
    ".pdf": "raw",
    ".xlsx": "raw",
    ".xls": "raw",
    ".csv": "raw",
    ".docx": "raw",
}


# ---------------------------------------------------------------------------
# Cloudinary helpers
# ---------------------------------------------------------------------------


def _upload_to_cloudinary(file: UploadFile, user_id: int, ext: str) -> dict:
    """
    Stream the uploaded file to Cloudinary via a short-lived temp file.
    The temp file is deleted immediately after upload.

    Folder structure on Cloudinary:
        sha_claims/<user_id>/<uuid4>

    Returns the full Cloudinary upload response dict containing
    `secure_url`, `public_id`, `bytes`, etc.
    """
    public_id = f"sha_claims/{user_id}/{uuid.uuid4().hex}"
    resource_type = CLOUDINARY_RESOURCE_TYPES.get(ext, "raw")

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = tmp.name

        result = cloudinary.uploader.upload(
            tmp_path,
            public_id=public_id,
            resource_type=resource_type,
            tags=[f"user_{user_id}", "sha_claim"],
            context={"original_filename": file.filename},
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Cloudinary upload failed: {exc}",
        )
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)

    return result


def _download_from_cloudinary(secure_url: str, ext: str) -> str:
    """
    Download a Cloudinary file to a local temp file for extraction/parsing.
    Returns the temp file path. Caller must delete it after use.
    """

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp_path = tmp.name
            with httpx.stream("GET", secure_url) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    tmp.write(chunk)
    except Exception as exc:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to retrieve file from Cloudinary: {exc}",
        )

    return tmp_path


# ---------------------------------------------------------------------------
# POST /upload-and-extract
# ---------------------------------------------------------------------------


@router.post("/upload-and-extract", response_model=ExtractResponse)
async def upload_and_extract(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Upload a SHA claim file to Cloudinary and immediately extract structured data.

    Flow:
      1. Validate file extension
      2. Upload file to Cloudinary  →  stored permanently at sha_claims/<user_id>/
      3. Download file from Cloudinary to a short-lived temp file
      4. Run SHAClaimExtractor on the temp file
      5. Delete temp file
      6. Return extracted data + Cloudinary references to the frontend

    The frontend must store `cloudinary_public_id` and `cloudinary_url` and
    pass them back in the /submit request so the DB record links to the file.
    """
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    # ── 1. Upload to Cloudinary ───────────────────────────────────────────
    cloudinary_result = _upload_to_cloudinary(file, current_user.id, ext)
    secure_url = cloudinary_result["secure_url"]
    public_id = cloudinary_result["public_id"]
    file_size = cloudinary_result["bytes"]

    # ── 2. Download to temp file for extraction ───────────────────────────
    tmp_path = _download_from_cloudinary(secure_url, ext)

    try:
        claim_data: SHAClaimData = sha_claim_extractor.extract_from_file(tmp_path)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Extraction failed: {exc}",
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)  # always clean up

    is_valid, validation_errors = sha_claim_extractor.validate(claim_data)

    return {
        "cloudinary_public_id": public_id,
        "cloudinary_url": secure_url,
        "file_size": file_size,
        "extracted": claim_data.to_dict(),
        "is_valid": is_valid,
        "validation_errors": validation_errors,
    }


# ---------------------------------------------------------------------------
# POST /submit
# ---------------------------------------------------------------------------


@router.post("/submit", response_model=ClaimResponse)
async def submit_claim(
    claim_data: ClaimCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Persist a validated SHA claim to the database and schedule fraud analysis.

    `claim_data` must include the `cloudinary_public_id` and `cloudinary_url`
    returned by /upload-and-extract so the Claim row links back to the source file.
    """
    provider = (
        db.query(Provider)
        .filter(Provider.provider_id == claim_data.provider_id)
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

    claim_number = sha_claim_extractor.generate_claim_number(claim_data.provider_id)

    db_claim = Claim(
        claim_number=claim_number,
        # Cloudinary file reference — stored on every claim for audit trail
        source_file_url=claim_data.cloudinary_url,
        source_file_public_id=claim_data.cloudinary_public_id,
        # Part I — Provider
        provider_id=provider.id,
        provider_code=claim_data.provider_id,
        provider_name=claim_data.provider_name,
        # Part II — Patient
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
        visit_type=claim_data.visit_type,
        visit_admission_date=claim_data.visit_admission_date,
        op_ip_number=claim_data.op_ip_number,
        new_or_return_visit=claim_data.new_or_return_visit,
        discharge_date=claim_data.discharge_date,
        rendering_physician=claim_data.rendering_physician,
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
        # Field 14 — Benefit lines (JSONB)
        benefit_lines=(
            [line.dict() for line in claim_data.benefit_lines]
            if claim_data.benefit_lines
            else []
        ),
        total_bill_amount=claim_data.total_bill_amount,
        total_claim_amount=claim_data.total_claim_amount,
        # Declaration
        patient_authorised_name=claim_data.patient_authorised_name,
        declaration_date=claim_data.declaration_date,
        # System fields
        submitted_by=current_user.id,
        submission_date=datetime.utcnow(),
        status=ClaimStatus.PROCESSING,
    )

    db.add(db_claim)
    db.commit()
    db.refresh(db_claim)

    background_tasks.add_task(analyze_claim_background, db_claim.id)

    return db_claim


# ---------------------------------------------------------------------------
# POST /bulk-upload
# ---------------------------------------------------------------------------


@router.post("/bulk-upload", response_model=BulkUploadResponse)
async def bulk_upload_claims(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    """
    Bulk upload claims from a CSV or Excel file.

    Flow:
      1. Upload the bulk file to Cloudinary (audit trail)
      2. Download it to a temp file for pandas parsing
      3. Validate columns and visit_type values across all rows
      4. Insert each row as a Claim linked to the Cloudinary file
      5. Schedule fraud analysis per claim
    """
    ext = Path(file.filename).suffix.lower()
    if ext not in {".csv", ".xlsx", ".xls"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bulk upload accepts CSV or Excel files only.",
        )

    # ── 1. Upload bulk file to Cloudinary ────────────────────────────────
    cloudinary_result = _upload_to_cloudinary(file, current_user.id, ext)
    bulk_file_url = cloudinary_result["secure_url"]
    bulk_file_public_id = cloudinary_result["public_id"]

    # ── 2. Download and parse ────────────────────────────────────────────
    tmp_path = _download_from_cloudinary(bulk_file_url, ext)
    try:
        df = pd.read_csv(tmp_path) if ext == ".csv" else pd.read_excel(tmp_path)
        df = df.fillna("")
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not parse file: {exc}",
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    # ── 3. Validate columns ──────────────────────────────────────────────
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

    valid_visit_types = {"inpatient", "outpatient", "day-care", "day care"}
    invalid_rows = df[~df["visit_type"].str.lower().isin(valid_visit_types)]
    if not invalid_rows.empty:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid visit_type in rows: {list(invalid_rows.index + 2)}. "
                "Must be one of: Inpatient, Outpatient, Day-care"
            ),
        )

    # ── 4. Insert claims ─────────────────────────────────────────────────
    claim_ids: List[int] = []
    errors: List[dict] = []

    for idx, row in df.iterrows():
        row_num = int(idx) + 2
        try:
            provider = (
                db.query(Provider)
                .filter(Provider.provider_id == row["provider_id"])
                .first()
            )
            if not provider:
                errors.append(
                    {
                        "row": row_num,
                        "error": f"Provider '{row['provider_id']}' not found",
                    }
                )
                continue

            claim_number = sha_claim_extractor.generate_claim_number(
                str(row["provider_id"])
            )

            db_claim = Claim(
                claim_number=claim_number,
                # Every row links to the same bulk file on Cloudinary
                source_file_url=bulk_file_url,
                source_file_public_id=bulk_file_public_id,
                # Provider
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
                # Visit
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
                was_referred=str(row.get("was_referred", "")).lower()
                in {"yes", "true", "1"},
                referral_provider=str(row.get("referral_provider", "")),
                # Disposition & diagnoses
                patient_disposition=str(row.get("patient_disposition", "")),
                admission_diagnosis=str(row.get("admission_diagnosis", "")),
                discharge_diagnosis=str(row.get("discharge_diagnosis", "")),
                icd11_code=str(row.get("icd11_code", "")),
                related_procedure=str(row.get("related_procedure", "")),
                # Amounts
                total_bill_amount=float(row["total_bill_amount"]),
                total_claim_amount=float(row["total_claim_amount"]),
                submitted_by=current_user.id,
                submission_date=datetime.utcnow(),
                status=ClaimStatus.PROCESSING,
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
        "bulk_file_url": bulk_file_url,
        "message": (
            f"Successfully submitted {len(claim_ids)} claims. " f"{len(errors)} failed."
        ),
    }


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------


@router.get("/", response_model=List[ClaimResponse])
async def list_claims(
    claim_status: Optional[ClaimStatus] = None,
    is_flagged: Optional[bool] = None,
    provider_id: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List claims with optional filters. Max 200 results per page."""
    query = db.query(Claim)

    if claim_status:
        query = query.filter(Claim.status == claim_status)
    if is_flagged is not None:
        query = query.filter(Claim.is_flagged == is_flagged)
    if provider_id:
        query = query.filter(Claim.provider_code == provider_id)
    if date_from:
        query = query.filter(Claim.visit_admission_date >= date_from)
    if date_to:
        query = query.filter(Claim.visit_admission_date <= date_to)

    return (
        query.order_by(Claim.submission_date.desc())
        .offset(offset)
        .limit(min(limit, 200))
        .all()
    )


# ---------------------------------------------------------------------------
# GET /{claim_id}
# ---------------------------------------------------------------------------


@router.get("/{claim_id}", response_model=ClaimDetailResponse)
async def get_claim(
    claim_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retrieve full details for a single claim, including its Cloudinary file URL."""
    claim = db.query(Claim).filter(Claim.id == claim_id).first()
    if not claim:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Claim not found"
        )
    return claim


# ---------------------------------------------------------------------------
# GET /{claim_id}/risk-score
# ---------------------------------------------------------------------------


@router.get("/{claim_id}/risk-score", response_model=RiskScoreResponse)
async def get_claim_risk_score(
    claim_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the fraud risk score and flag details for a claim."""
    claim = db.query(Claim).filter(Claim.id == claim_id).first()
    if not claim:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Claim not found"
        )

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


@router.put("/{claim_id}/status", response_model=ClaimResponse)
async def update_claim_status(
    claim_id: int,
    update_data: ClaimUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a claim's status, processing notes, or approval details."""
    claim = db.query(Claim).filter(Claim.id == claim_id).first()
    if not claim:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Claim not found"
        )

    if update_data.status:
        claim.status = update_data.status
    if update_data.processing_notes:
        claim.processing_notes = update_data.processing_notes
    if update_data.approved_amount is not None:
        if update_data.approved_amount > claim.total_claim_amount:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="approved_amount cannot exceed total_claim_amount",
            )
        claim.approved_amount = update_data.approved_amount
        claim.approved_by = current_user.id
        claim.approved_at = datetime.utcnow()
    if update_data.rejection_reason:
        claim.rejection_reason = update_data.rejection_reason

    db.commit()
    db.refresh(claim)
    return claim
