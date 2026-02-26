from datetime import datetime
import uuid
from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.database import get_db
from app.models.claim_model import Claim
from app.models.provider_model import Provider
from app.models.member_model import Patient
from app.models.user_model import User
from app.models.enums_model import ClaimStatus, UserRole
from app.schemas.claim_schema import ClaimCreate, ClaimResponse
from app.services.fraud_analyzer import analyze_claim_background
from app.services.fraud_analyzer import analyze_claim_background

router = APIRouter()

@router.post("/simulate", response_model=ClaimResponse)
async def simulate_claim(
    claim_data: ClaimCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Simulate a claim submission from SHA.
    - Creates a dummy simulation user if it doesn't exist.
    - Creates/gets the provider specified in claim_data.
    - Creates/gets the patient specified in claim_data.
    - Saves the claim and triggers the fraud detection engine.
    - Map SHA-specific fields into raw_payload as requested.
    """
    # 1. Get or Create Simulation User
    sim_user_email = "simulation@sha.go.ke"
    result = await db.execute(select(User).filter(User.email == sim_user_email))
    user = result.scalars().first()
    if not user:
        user = User(
            email=sim_user_email,
            hashed_password="simulation_hashed_password", # Dummy
            first_name="SHA",
            last_name="Simulator",
            phone_number="0000000000",
            role=UserRole.ANALYST,
            is_active=True
        )
        db.add(user)
        await db.flush()

    # 2. Get or Create Provider
    result = await db.execute(select(Provider).filter(Provider.sha_provider_code == claim_data.provider_id))
    provider = result.scalars().first()
    if not provider:
        provider = Provider(
            sha_provider_code=claim_data.provider_id,
            name=claim_data.provider_name or f"Simulated Provider {claim_data.provider_id}",
            county="Nairobi", # Default for simulation
            facility_type="Level 4",
            accreditation_status="Accredited"
        )
        db.add(provider)
        await db.flush()

    # 3. Get or Create Patient
    patient = None
    if claim_data.sha_number:
        result = await db.execute(select(Patient).filter(Patient.sha_number == claim_data.sha_number))
        patient = result.scalars().first()
        if not patient:
            patient = Patient(
                sha_number=claim_data.sha_number,
                date_of_birth=datetime(1990, 1, 1), # Placeholder
                gender="M", # Placeholder
                county="Nairobi"
            )
            db.add(patient)
            await db.flush()
    
    if not patient:
        # Fallback if no SHA number provided (unlikely in SHI 2023 forms)
        # We need a patient record for the FK constraint in Claim
        patient = Patient(
            sha_number=f"SIM-{uuid.uuid4().hex[:8]}",
            date_of_birth=datetime(1990, 1, 1),
            gender="M",
            county="Nairobi"
        )
        db.add(patient)
        await db.flush()

    # 4. Create Claim Number
    sha_claim_id = f"SHA-SIM-{uuid.uuid4().hex[:12].upper()}"

    # 5. Prepare raw_payload for SHA-specific fields not in the existing model
    raw_payload = claim_data.model_dump()
    raw_payload["simulated_at"] = datetime.utcnow().isoformat()

    # 6. Create Claim Record mapping fields to EXISTING model
    db_claim = Claim(
        sha_claim_id=sha_claim_id,
        provider_id=provider.id,
        patient_id=patient.id,
        claim_type=claim_data.visit_type.value if claim_data.visit_type else "OP",
        admission_date=claim_data.visit_admission_date,
        discharge_date=claim_data.discharge_date,
        diagnosis_codes=[claim_data.icd11_code] if claim_data.icd11_code else [],
        total_claim_amount=float(claim_data.total_claim_amount),
        sha_status=ClaimStatus.PROCESSING,
        raw_payload=raw_payload,
        submitted_by=user.id,
        submitted_at=datetime.utcnow()
    )
    
    db.add(db_claim)
    await db.commit()
    await db.refresh(db_claim)

    # 7. Trigger Analysis (Using the async background task)
    background_tasks.add_task(analyze_claim_background, db_claim.id)

    return db_claim
