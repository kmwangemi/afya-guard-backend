from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums_model import ModelType


class ModelVersion(Base):
    """
    Registry of trained ML model versions.
    Supports versioning, performance tracking, and controlled deployment.
    Only one model should be active (deployed) at a time.
    """

    __tablename__ = "model_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    version_name: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
        comment="Human-readable version tag, e.g. 'xgboost-v2.1-2026Q1'",
    )
    model_type: Mapped[ModelType] = mapped_column(
        Enum(ModelType, name="model_type_enum"),
        nullable=False,
    )
    description: Mapped[Optional[str]] = mapped_column(Text)
    # Training window
    training_start: Mapped[Optional[date]] = mapped_column(Date)
    training_end: Mapped[Optional[date]] = mapped_column(Date)
    training_sample_size: Mapped[Optional[int]] = mapped_column(
        Integer, comment="Number of claims used to train this model"
    )
    # Storage
    model_artifact_path: Mapped[Optional[str]] = mapped_column(
        String(500), comment="S3 / MinIO path to the serialised model artifact"
    )
    # Performance metrics stored as JSONB for flexibility
    performance_metrics: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        comment='e.g. {"auc_roc": 0.94, "precision": 0.87, "recall": 0.81, "f1": 0.84}',
    )
    feature_names: Mapped[Optional[list]] = mapped_column(
        JSONB, comment="Ordered list of feature names the model was trained on"
    )
    # Deployment state
    is_deployed: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="True = this version is actively used for scoring",
    )
    deployed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    deployed_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    def __repr__(self) -> str:
        return f"<ModelVersion {self.version_name} deployed={self.is_deployed}>"
