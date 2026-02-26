"""
SHA Fraud Detection — Rules, Models & Alert Schemas

Covers: fraud rule CRUD, model version management, fraud alerts.
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import Field

from app.models.enums_model import (
    AlertChannel,
    AlertSeverity,
    AlertStatus,
    AlertType,
    DeliveryStatus,
    ModelType,
)
from app.schemas.base_schema import BaseSchema, TimestampMixin, UUIDSchema

# ══════════════════════════════════════════════════════════════════════════════
# FRAUD RULES
# ══════════════════════════════════════════════════════════════════════════════


class FraudRuleCreate(BaseSchema):
    rule_name: str = Field(min_length=3, max_length=100)
    display_name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    weight: float = Field(
        ge=0, le=100, description="Score contribution (0-100) when rule fires"
    )
    config: Dict[str, Any] = Field(
        description='Rule expression e.g. {"field": "duplicate_within_7d", "operator": "is_true"}'
    )
    is_active: bool = True


class FraudRuleUpdate(BaseSchema):
    display_name: Optional[str] = None
    description: Optional[str] = None
    weight: Optional[float] = Field(None, ge=0, le=100)
    config: Optional[Dict[str, Any]] = None
    category: Optional[str] = None


class FraudRuleResponse(UUIDSchema, TimestampMixin):
    rule_name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    weight: float
    config: Dict[str, Any]
    is_active: bool


class RuleToggleResponse(BaseSchema):
    rule_name: str
    is_active: bool
    message: str


# ══════════════════════════════════════════════════════════════════════════════
# MODEL VERSIONS
# ══════════════════════════════════════════════════════════════════════════════


class ModelVersionCreate(BaseSchema):
    version_name: str = Field(min_length=3, max_length=100)
    model_type: ModelType
    description: Optional[str] = None
    training_start: Optional[datetime] = None
    training_end: Optional[datetime] = None
    training_sample_size: Optional[int] = Field(None, ge=1)
    model_artifact_path: Optional[str] = None
    performance_metrics: Optional[Dict[str, Any]] = Field(
        None, description='e.g. {"auc_roc": 0.94, "precision": 0.87, "recall": 0.81}'
    )
    feature_names: Optional[List[str]] = None


class ModelVersionResponse(UUIDSchema, TimestampMixin):
    version_name: str
    model_type: ModelType
    description: Optional[str] = None
    training_start: Optional[datetime] = None
    training_end: Optional[datetime] = None
    training_sample_size: Optional[int] = None
    model_artifact_path: Optional[str] = None
    performance_metrics: Optional[Dict[str, Any]] = None
    feature_names: Optional[List[str]] = None
    is_deployed: bool
    deployed_at: Optional[datetime] = None


class ModelDeployResponse(BaseSchema):
    version_name: str
    is_deployed: bool
    deployed_at: datetime
    message: str


# ══════════════════════════════════════════════════════════════════════════════
# FRAUD ALERTS
# ══════════════════════════════════════════════════════════════════════════════


class AlertNotificationResponse(UUIDSchema):
    channel: AlertChannel
    recipient: Optional[str] = None
    delivery_status: DeliveryStatus
    attempt_count: int
    last_attempt_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    response_code: Optional[int] = None
    error_message: Optional[str] = None


class FraudAlertResponse(UUIDSchema):
    claim_id: uuid.UUID
    fraud_score_id: Optional[uuid.UUID] = None
    fraud_case_id: Optional[uuid.UUID] = None
    alert_type: AlertType
    severity: AlertSeverity
    status: AlertStatus
    title: str
    message: str
    triggered_by: Optional[str] = None
    score_at_alert: Optional[float] = None
    assigned_to: Optional[uuid.UUID] = None
    assigned_analyst_name: Optional[str] = None
    auto_escalate: bool
    auto_escalate_after_hours: Optional[int] = None
    escalated_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    is_false_positive: Optional[bool] = None
    resolution_note: Optional[str] = None
    raised_at: datetime
    acknowledged_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    fraud_alert_metadata: Optional[Dict[str, Any]] = None
    notifications: List[AlertNotificationResponse] = []
    # Denormalised for quick display
    sha_claim_id: Optional[str] = None
    provider_name: Optional[str] = None


class AlertAcknowledgeRequest(BaseSchema):
    note: Optional[str] = None


class AlertResolveRequest(BaseSchema):
    resolution_note: str = Field(min_length=5)
    is_false_positive: bool = False


class AlertAssignRequest(BaseSchema):
    assigned_to: uuid.UUID


class AlertListFilter(BaseSchema):
    status: Optional[AlertStatus] = None
    severity: Optional[AlertSeverity] = None
    alert_type: Optional[AlertType] = None
    assigned_to: Optional[uuid.UUID] = None
    raised_from: Optional[datetime] = None
    raised_to: Optional[datetime] = None


# ══════════════════════════════════════════════════════════════════════════════
# ANALYTICS
# ══════════════════════════════════════════════════════════════════════════════


class AnalyticsSummary(BaseSchema):
    total_claims: int
    total_scored: int
    flagged_count: int
    flagged_percent: float
    high_risk_count: int
    critical_risk_count: int
    open_cases: int
    confirmed_fraud_count: int
    estimated_savings_kes: float
    avg_score: float


class RiskDistributionItem(BaseSchema):
    risk_level: str
    count: int
    percent: float


class ProviderAnalytics(BaseSchema):
    provider_id: uuid.UUID
    provider_name: str
    sha_provider_code: str
    total_claims: int
    flagged_claims: int
    avg_score: float
    avg_claim_amount: float
    peer_avg_amount: float
    deviation_percent: float
    high_risk_flag: bool
