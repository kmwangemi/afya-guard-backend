from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.v1.database import get_db
from src.api.v1.models.claim_model import Claim
from src.api.v1.schemas.claim_schema import ClaimCreate, ClaimResponse, ClaimUpdate

claim_router = APIRouter()

DbDependency = Annotated[AsyncSession, Depends(get_db)]


@claim_router.post(
    "/claims", response_model=ClaimResponse, status_code=status.HTTP_201_CREATED
)
async def create_claim(
    claim_data: ClaimCreate,
    db: DbDependency,
):
    try:
        # 🔍 Check if claim_number already exists
        stmt = select(Claim).where(Claim.claim_number == claim_data.claim_number)
        existing_claim = (await db.execute(stmt)).scalar_one_or_none()
        if existing_claim:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Claim with this claim number already exists.",
            )
        # ✅ Create new claim
        new_claim = Claim(
            claim_number=claim_data.claim_number,
            patient_national_id=claim_data.patient_national_id,
            provider_id=claim_data.provider_id,
            diagnosis_code=claim_data.diagnosis_code,
            procedure_code=claim_data.procedure_code,
            claim_amount=claim_data.claim_amount,
            service_date=claim_data.service_date,
            submission_date=claim_data.submission_date,
        )
        db.add(new_claim)
        await db.commit()
        await db.refresh(new_claim)
        return new_claim
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create claim.",
        ) from e


@claim_router.get(
    "/claims/{claim_id}",
    response_model=ClaimResponse,
    status_code=status.HTTP_200_OK,
)
async def get_claim(
    claim_id: int,
    db: DbDependency,
):
    stmt = select(Claim).where(Claim.id == claim_id)
    result = await db.execute(stmt)  # 👈 await FIRST
    claim = result.scalar_one_or_none()  # 👈 then call scalar()
    if not claim:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Claim not found.",
        )
    return claim


@claim_router.get(
    "/claims",
    response_model=list[ClaimResponse],
    status_code=status.HTTP_200_OK,
)
async def get_all_claims(
    db: DbDependency,
):
    stmt = select(Claim)
    result = await db.execute(stmt)  # 👈 await FIRST
    claims = result.scalars().all()  # 👈 then call scalars()
    return claims


@claim_router.patch(
    "/claims/{claim_id}",
    response_model=ClaimResponse,
    status_code=status.HTTP_200_OK,
)
async def update_claim(
    claim_id: int,
    claim_data: ClaimUpdate,
    db: DbDependency,
):
    stmt = select(Claim).where(Claim.id == claim_id)
    result = await db.execute(stmt)
    claim = result.scalar_one_or_none()
    if not claim:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Claim not found.",
        )
    try:
        for field, value in claim_data.model_dump(exclude_unset=True).items():
            setattr(claim, field, value)
        await db.commit()
        await db.refresh(claim)
        return claim
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update claim.",
        ) from e


@claim_router.delete(
    "/claims/{claim_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_claim(
    claim_id: int,
    db: DbDependency,
):
    stmt = select(Claim).where(Claim.id == claim_id)
    result = await db.execute(stmt)
    claim = result.scalar_one_or_none()
    if not claim:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Claim not found.",
        )
    try:
        await db.delete(claim)
        await db.commit()
        return None
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete claim.",
        ) from e
