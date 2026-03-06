"""
SHA Fraud Detection — Audit Log Schema

Place at: app/schemas/log_schema.py

Shapes the GET /api/v1/logs response for the admin Logs page.
"""

from datetime import datetime
from typing import Any, Dict, Optional

from app.schemas.base_schema import BaseSchema


class AuditLogResponse(BaseSchema):
    """
    One row in the admin Logs table.

    Timestamp | User | Action | Entity Type | Entity ID | IP Address
    """

    id: str
    # Who
    user_id: Optional[str] = None  # NULL for system actions
    user_full_name: Optional[str] = None  # joined from users table
    user_email: Optional[str] = None
    # What
    action: str  # AuditAction value e.g. "LOGIN"
    # Which entity
    entity_type: Optional[str] = None  # "Claim" | "FraudCase" | "User"
    entity_id: Optional[str] = None  # UUID string of affected record
    # Context
    audit_log_metadata: Dict[str, Any] = {}
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    # When
    performed_at: datetime
