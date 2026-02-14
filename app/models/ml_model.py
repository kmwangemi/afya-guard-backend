import uuid
from datetime import UTC, datetime
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class MLModel(Base):
    """Machine Learning Model Tracking"""

    __tablename__ = "ml_models"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        unique=True,
        nullable=False,
    )
    model_name: Mapped[Optional[str]] = mapped_column(
        String(100), unique=True
    )  # upcoding_detector_v1
    model_type: Mapped[Optional[str]] = mapped_column(
        String(50)
    )  # random_forest, xgboost, etc.
    model_version: Mapped[Optional[str]] = mapped_column(String(20))
    model_path: Mapped[Optional[str]] = mapped_column(
        String(500)
    )  # Path to serialized model file
    # Performance Metrics
    accuracy: Mapped[Optional[float]] = mapped_column(Float)
    precision: Mapped[Optional[float]] = mapped_column(Float)
    recall: Mapped[Optional[float]] = mapped_column(Float)
    f1_score: Mapped[Optional[float]] = mapped_column(Float)
    # Training Information
    training_samples: Mapped[Optional[int]] = mapped_column(Integer)
    training_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    features_used: Mapped[Optional[Any]] = mapped_column(JSON)
    hyperparameters: Mapped[Optional[Any]] = mapped_column(JSON)
    # Deployment
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    deployed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    def __repr__(self) -> str:
        return f"<MLModel(id={self.id}, model_name={self.model_name}, model_type={self.model_type}, version={self.model_version})>"
