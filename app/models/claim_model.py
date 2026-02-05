from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    claim_number: Mapped[str] = mapped_column(String, unique=True, index=True)
    patient_national_id: Mapped[str] = mapped_column(String, index=True)
    provider_id: Mapped[str] = mapped_column(String, index=True)
    diagnosis_code: Mapped[str] = mapped_column(String)  # ICD-11
    procedure_code: Mapped[str] = mapped_column(String)  # CPT
    claim_amount: Mapped[float] = mapped_column(Float)
    service_date: Mapped[datetime] = mapped_column(DateTime)
    submission_date: Mapped[datetime] = mapped_column(DateTime)
    # =========================
    # Fraud detection results
    # =========================
    risk_score: Mapped[float] = mapped_column(Float, default=0)
    is_flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    fraud_flags: Mapped[Optional[list[Any]]] = mapped_column(
        JSON, nullable=True
    )  # Array of detected issues
    status: Mapped[str] = mapped_column(
        String, default="pending"
    )  # pending, approved, rejected
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
