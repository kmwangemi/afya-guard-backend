from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class FraudRule(Base):
    """
    Configurable deterministic fraud rules.
    Rules are stored as JSONB config so they can be edited via admin dashboard
    without code deploys. Each rule contributes a weighted score.

    Example config:
        {"field": "duplicate_within_7d", "operator": "is_true", "value": null}
        {"field": "provider_cost_zscore", "operator": "greater_than", "value": 3.0}
    """

    __tablename__ = "fraud_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    rule_name: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
        comment="Unique machine-readable name, e.g. 'duplicate_claim_7d'",
    )
    display_name: Mapped[Optional[str]] = mapped_column(
        String(200), comment="Human-readable label for the dashboard"
    )
    description: Mapped[Optional[str]] = mapped_column(Text)
    category: Mapped[Optional[str]] = mapped_column(
        String(50),
        comment="'duplicate' | 'upcoding' | 'provider' | 'member' | 'timing'",
    )
    # Scoring
    weight: Mapped[float] = mapped_column(
        Numeric(6, 4),
        nullable=False,
        comment="Score contribution (0-100) when this rule fires",
    )
    # Rule logic as JSONB
    config: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        comment='Rule expression: {"field": "...", "operator": "...", "value": ...}',
    )
    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    def __repr__(self) -> str:
        return f"<FraudRule '{self.rule_name}' weight={self.weight} active={self.is_active}>"
