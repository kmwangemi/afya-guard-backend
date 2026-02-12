from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        unique=True,
        nullable=False,
    )
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

    def __repr__(self) -> str:
        return f"<Claim(id={self.id}, claim_number={self.claim_number}, risk_score={self.risk_score}, is_flagged={self.is_flagged})>"
