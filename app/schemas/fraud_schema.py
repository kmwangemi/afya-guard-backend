"""
SHA Fraud Detection — Fraud Scoring Schemas

Covers: fraud scores, explanations, manual score override, high-risk listing.
"""

import uuid
from datetime import datetime
from typing import Dict, List, Optional

from pydantic import Field

from app.models.enums_model import RiskLevel
from app.schemas.base_schema import BaseSchema, UUIDSchema

# ── Explanation ───────────────────────────────────────────────────────────────


class FraudExplanationResponse(UUIDSchema):
    explanation: str
    feature_name: Optional[str] = None
    feature_value: Optional[str] = None
    weight: Optional[float] = None
    source: Optional[str] = None


# ── Fraud Score ───────────────────────────────────────────────────────────────


class FraudScoreResponse(UUIDSchema):
    claim_id: uuid.UUID
    rule_score: Optional[float] = None
    ml_probability: Optional[float] = None
    anomaly_score: Optional[float] = None
    detector_scores: Optional[Dict[str, float]] = None
    final_score: Optional[float] = None
    risk_level: Optional[RiskLevel] = None
    scored_at: datetime
    scored_by: Optional[str] = None
    model_version_id: Optional[uuid.UUID] = None
    explanations: List[FraudExplanationResponse] = []


class ScoreOverrideRequest(BaseSchema):
    """
    Allow a senior analyst to manually override the final score.
    Logged in audit trail.
    """

    override_score: float = Field(ge=0, le=100)
    override_risk_level: RiskLevel
    reason: str = Field(
        min_length=10, description="Required justification for override"
    )


class ScoreRequest(BaseSchema):
    """Trigger scoring for a claim (usually done automatically)."""

    claim_id: uuid.UUID
    force_rescore: bool = Field(
        default=False,
        description="If True, re-score even if a recent score already exists",
    )


class HighRiskClaimsFilter(BaseSchema):
    """Filters for GET /fraud/high-risk."""

    risk_level: Optional[RiskLevel] = None
    provider_id: Optional[uuid.UUID] = None
    scored_from: Optional[datetime] = None
    scored_to: Optional[datetime] = None
    min_score: Optional[float] = Field(None, ge=0, le=100)


class HighRiskClaimResponse(BaseSchema):
    """Summary row for high-risk claims list."""

    claim_id: uuid.UUID
    sha_claim_id: str
    final_score: Optional[float] = None
    risk_level: Optional[RiskLevel] = None
    provider_name: Optional[str] = None
    member_sha_id: Optional[str] = None
    total_claim_amount: Optional[float] = None
    scored_at: Optional[datetime] = None
    has_open_case: bool = False
    top_explanations: List[str] = []
