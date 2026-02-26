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


class UserUpdate(BaseSchema):
    """Partial update — all fields optional."""

    full_name: Optional[str] = Field(None, min_length=2, max_length=255)
    phone: Optional[str] = None
    is_active: Optional[bool] = None


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
    roles: List[RoleResponse] = []


class UserListResponse(BaseSchema):
    """Slim version for list endpoints (no nested permissions)."""

    id: uuid.UUID
    email: str
    full_name: str
    is_active: bool
    is_superuser: bool
    last_login_at: Optional[datetime] = None
    roles: List[str] = []  # just role names for performance
