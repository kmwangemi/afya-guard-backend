"""
SHA Fraud Detection System — Enum Definitions

All PostgreSQL-native ENUMs used across the system.
Using str + enum.Enum for clean JSON serialization and SQLAlchemy compatibility.
"""

import enum

# ---------------------------------------------------------------------------
# Claim Domain
# ---------------------------------------------------------------------------


class ClaimStatus(str, enum.Enum):
    """Status of a claim as tracked in the SHA system."""

    SUBMITTED = "SUBMITTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    FLAGGED = "FLAGGED"
    PAID = "PAID"


class ClaimType(str, enum.Enum):
    """Type of healthcare claim."""

    INPATIENT = "INPATIENT"
    OUTPATIENT = "OUTPATIENT"
    EMERGENCY = "EMERGENCY"
    MATERNITY = "MATERNITY"
    DENTAL = "DENTAL"
    OPTICAL = "OPTICAL"


class AccreditationStatus(str, enum.Enum):
    """Provider accreditation standing with SHA."""

    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"
    PENDING = "PENDING"


class FacilityType(str, enum.Enum):
    """Category of healthcare facility."""

    PUBLIC_HOSPITAL = "PUBLIC_HOSPITAL"
    PRIVATE_HOSPITAL = "PRIVATE_HOSPITAL"
    FAITH_BASED = "FAITH_BASED"
    CLINIC = "CLINIC"
    LABORATORY = "LABORATORY"
    PHARMACY = "PHARMACY"
    SPECIALIST_CENTER = "SPECIALIST_CENTER"


class Gender(str, enum.Enum):
    """Member gender."""

    MALE = "MALE"
    FEMALE = "FEMALE"
    OTHER = "OTHER"


# ---------------------------------------------------------------------------
# Fraud Scoring Domain
# ---------------------------------------------------------------------------


class RiskLevel(str, enum.Enum):
    """Fraud risk classification for a scored claim."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


# ---------------------------------------------------------------------------
# Case Management Domain
# ---------------------------------------------------------------------------


class CaseStatus(str, enum.Enum):
    """Lifecycle state of a fraud investigation case."""

    OPEN = "OPEN"
    UNDER_REVIEW = "UNDER_REVIEW"
    CONFIRMED_FRAUD = "CONFIRMED_FRAUD"
    CLEARED = "CLEARED"
    CLOSED = "CLOSED"


class CasePriority(str, enum.Enum):
    """Priority level assigned to a fraud case."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    URGENT = "URGENT"


# ---------------------------------------------------------------------------
# Model & Rules Domain
# ---------------------------------------------------------------------------


class ModelType(str, enum.Enum):
    """Type of ML model registered in the system."""

    XGBOOST = "XGBOOST"
    LIGHTGBM = "LIGHTGBM"
    ISOLATION_FOREST = "ISOLATION_FOREST"
    AUTOENCODER = "AUTOENCODER"
    LOGISTIC = "LOGISTIC"


class RuleOperator(str, enum.Enum):
    """Operators available in configurable fraud rule expressions."""

    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"
    GREATER_OR_EQUAL = "greater_or_equal"
    LESS_OR_EQUAL = "less_or_equal"
    IN = "in"
    NOT_IN = "not_in"
    IS_TRUE = "is_true"
    IS_FALSE = "is_false"


# ---------------------------------------------------------------------------
# RBAC / Auth Domain
# ---------------------------------------------------------------------------


class TokenType(str, enum.Enum):
    """JWT token classification."""

    ACCESS = "access"
    REFRESH = "refresh"


# ---------------------------------------------------------------------------
# Audit Domain
# ---------------------------------------------------------------------------


class AuditAction(str, enum.Enum):
    """Standardised action codes for the audit log."""

    # Auth
    LOGIN = "LOGIN"
    LOGOUT = "LOGOUT"
    TOKEN_REFRESHED = "TOKEN_REFRESHED"
    PASSWORD_CHANGED = "PASSWORD_CHANGED"

    # User management
    USER_CREATED = "USER_CREATED"
    USER_UPDATED = "USER_UPDATED"
    USER_DEACTIVATED = "USER_DEACTIVATED"
    ROLE_ASSIGNED = "ROLE_ASSIGNED"
    ROLE_REMOVED = "ROLE_REMOVED"

    # Claims
    CLAIM_INGESTED = "CLAIM_INGESTED"
    CLAIM_STATUS_UPDATED = "CLAIM_STATUS_UPDATED"
    FEATURES_COMPUTED = "FEATURES_COMPUTED"

    # Fraud
    CLAIM_SCORED = "CLAIM_SCORED"
    SCORE_OVERRIDDEN = "SCORE_OVERRIDDEN"

    # Cases
    CASE_CREATED = "CASE_CREATED"
    CASE_ASSIGNED = "CASE_ASSIGNED"
    CASE_STATUS_UPDATED = "CASE_STATUS_UPDATED"
    CASE_NOTE_ADDED = "CASE_NOTE_ADDED"
    CASE_CLOSED = "CASE_CLOSED"

    # Admin
    RULE_CREATED = "RULE_CREATED"
    RULE_UPDATED = "RULE_UPDATED"
    RULE_TOGGLED = "RULE_TOGGLED"
    MODEL_REGISTERED = "MODEL_REGISTERED"
    MODEL_DEPLOYED = "MODEL_DEPLOYED"


class AlertType(str, enum.Enum):
    """
    What kind of event triggered this alert.
    Each type maps to a specific detector or scoring threshold.
    """

    # Score-based triggers
    HIGH_RISK_SCORE = "HIGH_RISK_SCORE"  # final_score >= 70
    CRITICAL_RISK_SCORE = "CRITICAL_RISK_SCORE"  # final_score >= 90
    # Detector-based triggers
    DUPLICATE_CLAIM = "DUPLICATE_CLAIM"  # DuplicateDetector fired
    PHANTOM_PATIENT = "PHANTOM_PATIENT"  # PhantomPatientDetector fired
    UPCODING_DETECTED = "UPCODING_DETECTED"  # UpcodingDetector fired
    PROVIDER_ANOMALY = "PROVIDER_ANOMALY"  # ProviderProfiler fired
    # Rule-based triggers
    RULE_THRESHOLD_BREACH = (
        "RULE_THRESHOLD_BREACH"  # Specific fraud rule exceeded weight
    )
    # Behavioural triggers
    MEMBER_FREQUENCY_ABUSE = (
        "MEMBER_FREQUENCY_ABUSE"  # Member visiting too many facilities
    )
    PROVIDER_CLAIM_SPIKE = (
        "PROVIDER_CLAIM_SPIKE"  # Provider submitted unusual volume of claims
    )
    LATE_NIGHT_SUBMISSION = (
        "LATE_NIGHT_SUBMISSION"  # Claim submitted outside normal hours
    )
    BULK_SUBMISSION = (
        "BULK_SUBMISSION"  # Many claims from same provider in short window
    )
    # System triggers
    MODEL_CONFIDENCE_LOW = (
        "MODEL_CONFIDENCE_LOW"  # ML model returned low-confidence prediction
    )
    RESUBMISSION_PATTERN = (
        "RESUBMISSION_PATTERN"  # Previously rejected claim resubmitted with minor edits
    )


class AlertStatus(str, enum.Enum):
    """Lifecycle state of an alert."""

    OPEN = "OPEN"  # Just raised, not yet seen
    ACKNOWLEDGED = "ACKNOWLEDGED"  # Analyst has seen it, no action yet
    INVESTIGATING = "INVESTIGATING"  # Analyst actively looking into it
    ESCALATED = "ESCALATED"  # Promoted to a full FraudCase
    RESOLVED = "RESOLVED"  # Dismissed as false positive or resolved
    EXPIRED = "EXPIRED"  # Unactioned past the auto-expire window


class AlertSeverity(str, enum.Enum):
    """
    Operational severity driving notification urgency.
    Maps loosely to RiskLevel but is alert-specific.
    """

    INFO = "INFO"  # Informational — worth noting
    WARNING = "WARNING"  # Analyst should review soon
    HIGH = "HIGH"  # Requires attention within the working day
    CRITICAL = "CRITICAL"  # Requires immediate action


class AlertChannel(str, enum.Enum):
    """Delivery channel for alert notifications."""

    DASHBOARD = "DASHBOARD"  # In-app notification panel
    EMAIL = "EMAIL"  # Email to assigned analyst / team
    SMS = "SMS"  # SMS to analyst's registered phone
    WEBHOOK = "WEBHOOK"  # POST to external integration (e.g. SHA portal)
    SLACK = "SLACK"  # Slack workspace notification


class DeliveryStatus(str, enum.Enum):
    """Whether the notification was successfully delivered on a channel."""

    PENDING = "PENDING"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"  # Channel disabled or no recipient configured
