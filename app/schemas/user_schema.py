"""
SHA Fraud Detection — User & RBAC Schemas

Covers: user CRUD, role management, permission listing.
"""

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import EmailStr, Field

from app.schemas.base_schema import BaseSchema, TimestampMixin, UUIDSchema

# ── Permission ────────────────────────────────────────────────────────────────


class PermissionResponse(UUIDSchema):
    name: str
    description: Optional[str] = None
    category: Optional[str] = None


# ── Role ──────────────────────────────────────────────────────────────────────


class RoleCreate(BaseSchema):
    name: str = Field(min_length=2, max_length=100)
    display_name: Optional[str] = None
    description: Optional[str] = None
    permission_ids: List[uuid.UUID] = []


class RoleResponse(UUIDSchema, TimestampMixin):
    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    is_system_role: bool
    permissions: List[PermissionResponse] = []


# ── User ──────────────────────────────────────────────────────────────────────


class UserCreate(BaseSchema):
    """Admin creates a new system user."""

    email: EmailStr
    full_name: str = Field(min_length=2, max_length=255)
    phone: Optional[str] = None
    password: str = Field(min_length=8)
    role_ids: List[uuid.UUID] = []
    is_superuser: bool = False
    department: Optional[str] = Field(
        None,
        max_length=100,
        description="e.g. 'Fraud Investigation', 'Data Science', 'Compliance'",
    )


class UserProfileUpdate(BaseSchema):
    """
    Self-update — authenticated user edits their own profile.
    Scoped to non-sensitive fields only; password/email/roles require
    separate admin endpoints.
    """

    full_name: Optional[str] = Field(None, min_length=2, max_length=255)
    phone: Optional[str] = None
    is_active: Optional[bool] = None
    department: Optional[str] = Field(None, max_length=100)


class AssignRolesRequest(BaseSchema):
    """Replace a user's roles entirely."""

    role_ids: List[uuid.UUID]


class UserResponse(UUIDSchema, TimestampMixin):
    email: str
    full_name: str
    phone: Optional[str] = None
    is_active: bool
    is_superuser: bool
    last_login_at: Optional[datetime] = None
    must_change_password: bool
    department: Optional[str] = None
    roles: List[RoleResponse] = []


class UserListResponse(BaseSchema):
    """Slim version for list endpoints (no nested permissions)."""

    id: uuid.UUID
    email: str
    full_name: str
    is_active: bool
    is_superuser: bool
    department: Optional[str] = None
    last_login_at: Optional[datetime] = None
    roles: List[str] = []  # just role names for performance


class UserPerformanceStats(BaseSchema):
    """
    Performance Statistics card (Performance_stats.png).

    Four tiles computed for the currently authenticated analyst:

      Cases Investigated        → FraudCase rows where assigned_to = me
      Alerts Reviewed           → FraudAlert rows where assigned_to = me
      Fraud Cases Confirmed     → FraudCase rows where assigned_to = me
                                  AND status = CONFIRMED_FRAUD
      Total Fraud Amount        → SUM(estimated_loss) on confirmed cases
      Recovered                   assigned to me  (displayed as "KES 2.4M")
    """

    cases_investigated: int = 0
    alerts_reviewed: int = 0
    fraud_cases_confirmed: int = 0
    total_fraud_amount_recovered: float = 0.0  # raw KES value
    total_fraud_amount_display: str = "KES 0"  # formatted "KES 2.4M"
