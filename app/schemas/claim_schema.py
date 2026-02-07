from datetime import datetime
from typing import Any, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# =========================
# Base schema
# =========================
class ClaimBase(BaseModel):
    claim_number: str = Field(..., json_schema_extra={"example": "CLM-2026-0001"})
    patient_national_id: str = Field(..., json_schema_extra={"example": "12345678"})
    provider_id: str = Field(..., json_schema_extra={"example": "PROV-001"})
    diagnosis_code: str = Field(
        ..., json_schema_extra={"example": "ICD11-BA00"}
    )  # ICD-11
    procedure_code: str = Field(..., json_schema_extra={"example": "CPT-99213"})  # CPT
    claim_amount: float = Field(..., gt=0, json_schema_extra={"example": 12500.50})
    service_date: datetime = Field(
        ..., json_schema_extra={"example": "2026-01-15T10:30:00"}
    )
    submission_date: datetime = Field(
        ..., json_schema_extra={"example": "2026-01-16T09:00:00"}
    )

    @field_validator(
        "claim_number",
        "patient_national_id",
        "provider_id",
        "diagnosis_code",
        "procedure_code",
        mode="before",
    )
    @classmethod
    def strip_whitespace(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value


# =========================
# Create schema
# =========================
class ClaimCreate(ClaimBase):
    """
    Schema used when submitting a new claim.
    Fraud-related fields are system-generated.
    """

    pass


# =========================
# Update schema
# =========================
class ClaimUpdate(BaseModel):
    """
    Schema used for updating claim status or fraud results.
    """

    risk_score: Optional[float] = Field(
        None, ge=0, le=100, json_schema_extra={"example": 72.5}
    )
    is_flagged: Optional[bool] = Field(None, json_schema_extra={"example": True})
    fraud_flags: Optional[List[Any]] = Field(
        None,
        json_schema_extra={"example": ["duplicate_claim", "unusual_amount"]},
    )
    status: Optional[str] = Field(
        None,
        json_schema_extra={"example": "approved"},
    )

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        allowed_statuses = {"pending", "approved", "rejected"}
        if value not in allowed_statuses:
            raise ValueError(f"Status must be one of {allowed_statuses}")
        return value


# =========================
# Response schema
# =========================
class ClaimResponse(ClaimBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    risk_score: float
    is_flagged: bool
    fraud_flags: Optional[List[Any]] = None
    status: str
    created_at: datetime
    updated_at: datetime
