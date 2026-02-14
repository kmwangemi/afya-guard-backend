
import enum

class ClaimStatus(str, enum.Enum):
    """Claim processing status"""
    PENDING = "pending"
    PROCESSING = "processing"
    AUTO_APPROVED = "auto_approved"
    FLAGGED_REVIEW = "flagged_review"
    FLAGGED_CRITICAL = "flagged_critical"
    APPROVED = "approved"
    REJECTED = "rejected"
    UNDER_INVESTIGATION = "under_investigation"


class FraudSeverity(str, enum.Enum):
    """Fraud flag severity levels"""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class VisitType(str, enum.Enum):
    """Type of patient visit"""
    INPATIENT = "inpatient"
    OUTPATIENT = "outpatient"
    DAYCARE = "daycare"


class PatientDisposition(str, enum.Enum):
    """Patient outcome upon discharge"""
    IMPROVED = "improved"
    RECOVERED = "recovered"
    LAMA = "leave_against_medical_advice"  # LAMA
    ABSCONDED = "absconded"
    DIED = "died"
