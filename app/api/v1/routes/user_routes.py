"""
SHA Fraud Detection — User Routes

GET    /api/v1/users
POST   /api/v1/users
GET    /api/v1/users/{id}
PATCH  /api/v1/users/{id}
PATCH  /api/v1/users/{id}/roles
PATCH  /api/v1/users/{id}/deactivate
GET    /api/v1/roles
GET    /api/v1/permissions
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import PaginationParams, get_db, require_permission
from app.schemas.base_schema import PaginatedResponse
from app.schemas.user_schema import (
    AssignRolesRequest,
    PermissionResponse,
    RoleResponse,
    UserCreate,
    UserListResponse,
    UserResponse,
    UserUpdate,
)
from app.services.user_service import UserService

router = APIRouter(prefix="/users", tags=["Users & RBAC"])


@router.get(
    "", response_model=PaginatedResponse[UserListResponse], summary="List all users"
)
async def list_users(
    is_active: Optional[bool] = Query(None),
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("manage_users")),
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
        pages=-(-total // pagination.page_size),
    )


@router.post(
    "", response_model=UserResponse, status_code=201, summary="Create a new user"
)
async def create_user(
    data: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("manage_users")),
):
    return await UserService.create_user(db, data, created_by=current_user)


@router.get("/{user_id}", response_model=UserResponse, summary="Get user by ID")
async def get_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("manage_users")),
):
    return await UserService.get_user(db, user_id)


@router.patch("/{user_id}", response_model=UserResponse, summary="Update user profile")
async def update_user(
    user_id: uuid.UUID,
    data: UserUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("manage_users")),
):
    return await UserService.update_user(db, user_id, data, updated_by=current_user)


@router.patch(
    "/{user_id}/roles", response_model=UserResponse, summary="Assign roles to user"
)
async def assign_roles(
    user_id: uuid.UUID,
    data: AssignRolesRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("manage_users")),
):
    return await UserService.assign_roles(db, user_id, data, assigned_by=current_user)


@router.patch(
    "/{user_id}/deactivate", response_model=UserResponse, summary="Deactivate a user"
)
async def deactivate_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("manage_users")),
):
    return await UserService.deactivate_user(db, user_id, deactivated_by=current_user)


# ── Roles & Permissions ───────────────────────────────────────────────────────


@router.get(
    "/roles/all",
    response_model=list[RoleResponse],
    tags=["Users & RBAC"],
    summary="List all roles",
)
async def list_roles(
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("manage_users")),
):
    roles = await UserService.list_roles(db)
    return [RoleResponse.model_validate(r) for r in roles]


@router.get(
    "/permissions/all",
    response_model=list[PermissionResponse],
    tags=["Users & RBAC"],
    summary="List all permissions",
)
async def list_permissions(
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("manage_users")),
):
    perms = await UserService.list_permissions(db)
    return [PermissionResponse.model_validate(p) for p in perms]
