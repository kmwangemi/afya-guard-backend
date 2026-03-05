"""
SHA Fraud Detection — User & RBAC Routes

Profile  (own account, any authenticated user):
  GET    /api/v1/users/me              Get own profile
  PATCH  /api/v1/users/me              Update own name / phone / department

User management  (admin, requires manage_users permission):
  GET    /api/v1/users                 List all users (paginated)
  POST   /api/v1/users                 Create a new user
  GET    /api/v1/users/{id}            Get user by ID
  PATCH  /api/v1/users/{id}            Update user fields
  PATCH  /api/v1/users/{id}/roles      Assign roles
  PATCH  /api/v1/users/{id}/deactivate Deactivate account

RBAC reference data:
  GET    /api/v1/roles                 List all roles
  GET    /api/v1/permissions           List all permissions

NOTE: /me routes are registered BEFORE /{id} so FastAPI does not
      interpret the literal string "me" as a UUID parameter.
"""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import (
    PaginationParams,
    get_current_user,
    get_db,
    require_permission,
)
from app.models.user_model import User
from app.schemas.base_schema import PaginatedResponse
from app.schemas.user_schema import (
    AssignRolesRequest,
    PermissionResponse,
    RoleResponse,
    UserCreate,
    UserListResponse,
    UserPerformanceStats,
    UserProfileUpdate,
    UserResponse,
)
from app.services.user_service import UserService

router = APIRouter(tags=["Users & RBAC"])


# ══════════════════════════════════════════════════════════════════════════════
# /me  — own profile  (no special permission, any authenticated user)
# ══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/users/profile",
    response_model=UserResponse,
    summary="Get my profile",
    description="""
Returns the full profile of the currently authenticated user.

**Returned fields:**
- `id`, `email`, `full_name`, `phone`, `department`
- `is_active`, `is_superuser`, `must_change_password`
- `last_login_at`, `created_at`, `updated_at`
- `roles[]` with nested `permissions[]`

No special permission required — every authenticated user can call this.
Use the JWT bearer token in the Authorization header.
""",
)
async def get_my_profile(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await UserService.get_profile(db, current_user)


@router.patch(
    "/users/profile",
    response_model=UserResponse,
    summary="Update my profile",
    description="""
Update the authenticated user's own profile.

**Editable fields** (all optional — send only what you want to change):
- `full_name`
- `phone`
- `department`  — e.g. "Fraud Investigation", "Data Science", "Compliance"

Email, password, roles, and activation status require separate admin endpoints.
""",
)
async def update_my_profile(
    data: UserProfileUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await UserService.update_profile(db, current_user, data)


@router.get(
    "/users/profile/stats",
    response_model=UserPerformanceStats,
    summary="Get my performance statistics",
    description="""
Returns the four Performance Statistics tiles for the authenticated analyst.

| Tile | Source |
|---|---|
| **Cases Investigated** | `FraudCase` rows where `assigned_to = me` (any status) |
| **Alerts Reviewed** | `FraudAlert` rows where `assigned_to = me` (any status) |
| **Fraud Cases Confirmed** | `FraudCase` assigned to me with `status = CONFIRMED_FRAUD` |
| **Total Fraud Amount Recovered** | `SUM(estimated_loss)` on confirmed cases assigned to me |

`total_fraud_amount_display` is pre-formatted as `"KES 2.4M"` / `"KES 850.0K"` / `"KES 320"`.
No special permission required — any authenticated analyst can call this.
""",
)
async def get_my_performance_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await UserService.get_performance_stats(db, current_user)


# ══════════════════════════════════════════════════════════════════════════════
# User management  (requires manage_users permission)
# ══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/users",
    response_model=PaginatedResponse[UserListResponse],
    summary="List all users",
)
async def list_users(
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("manage_users")),
):
    items, total = await UserService.list_users(
        db,
        is_active=is_active,
        offset=pagination.offset,
        limit=pagination.page_size,
    )
    return PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=-(-total // pagination.page_size) if total else 0,
    )


@router.post(
    "/users",
    response_model=UserResponse,
    status_code=201,
    summary="Create a new user",
)
async def create_user(
    data: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("manage_users")),
):
    return await UserService.create_user(db, data, created_by=current_user)


@router.get(
    "/users/{user_id}",
    response_model=UserResponse,
    summary="Get user by ID",
)
async def get_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("manage_users")),
):
    return await UserService.get_user(db, user_id)


@router.patch(
    "/users/{user_id}",
    response_model=UserResponse,
    summary="Update user (admin)",
    description="Admin update of any user field — `full_name`, `phone`, `department`, `is_active`.",
)
async def update_user(
    user_id: uuid.UUID,
    data: UserProfileUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("manage_users")),
):
    return await UserService.update_user(db, user_id, data, updated_by=current_user)


@router.patch(
    "/users/{user_id}/roles",
    response_model=UserResponse,
    summary="Assign roles to user",
    description="Replaces the user's entire role set with the provided list.",
)
async def assign_roles(
    user_id: uuid.UUID,
    data: AssignRolesRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("manage_users")),
):
    return await UserService.assign_roles(db, user_id, data, assigned_by=current_user)


@router.patch(
    "/users/{user_id}/deactivate",
    response_model=UserResponse,
    summary="Deactivate a user account",
    description="Soft-delete — sets `is_active = false`. Cannot deactivate your own account.",
)
async def deactivate_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("manage_users")),
):
    return await UserService.deactivate_user(db, user_id, deactivated_by=current_user)


# ══════════════════════════════════════════════════════════════════════════════
# RBAC reference data
# ══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/roles",
    response_model=List[RoleResponse],
    summary="List all roles",
)
async def list_roles(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("manage_users")),
):
    roles = await UserService.list_roles(db)
    return [RoleResponse.model_validate(r) for r in roles]


@router.get(
    "/permissions",
    response_model=List[PermissionResponse],
    summary="List all permissions",
)
async def list_permissions(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("manage_users")),
):
    perms = await UserService.list_permissions(db)
    return [PermissionResponse.model_validate(p) for p in perms]
