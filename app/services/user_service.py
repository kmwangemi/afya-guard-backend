"""
SHA Fraud Detection — User Service

Handles: user CRUD, role assignment, permission management.
"""

import uuid
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.enums_model import AuditAction
from app.models.permission_model import Permission
from app.models.role_model import Role
from app.models.user_model import User
from app.schemas.user_schema import (
    AssignRolesRequest,
    UserCreate,
    UserListResponse,
    UserResponse,
    UserUpdate,
)
from app.services.audit_service import AuditService


class UserService:

    @staticmethod
    async def create_user(
        db: AsyncSession,
        data: UserCreate,
        created_by: User,
    ) -> UserResponse:
        """Create a new system user with optional initial roles."""
        result = await db.execute(select(User).filter(User.email == data.email.lower()))
        if result.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"User with email '{data.email}' already exists",
            )
        roles = []
        if data.role_ids:
            result = await db.execute(select(Role).filter(Role.id.in_(data.role_ids)))
            roles = result.scalars().all()
            if len(roles) != len(data.role_ids):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="One or more roles not found",
                )
        user = User(
            email=data.email.lower(),
            full_name=data.full_name,
            phone=data.phone,
            hashed_password=hash_password(data.password),
            is_superuser=data.is_superuser,
            must_change_password=True,  # force password change on first login
        )
        user.roles = roles
        db.add(user)
        await db.commit()
        await db.refresh(user)
        await AuditService.log(
            db,
            AuditAction.USER_CREATED,
            user_id=created_by.id,
            entity_type="User",
            entity_id=user.id,
            metadata={"email": user.email, "roles": [r.name for r in roles]},
        )
        return UserResponse.model_validate(user)

    @staticmethod
    async def get_user(
        db: AsyncSession,
        user_id: uuid.UUID,
    ) -> UserResponse:
        """Fetch a single user by ID."""
        result = await db.execute(select(User).filter(User.id == user_id))
        user = result.scalars().first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )
        return UserResponse.model_validate(user)

    @staticmethod
    async def list_users(
        db: AsyncSession,
        is_active: Optional[bool] = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[UserListResponse], int]:
        """List users with optional active filter and pagination."""
        query = select(User)
        if is_active is not None:
            query = query.filter(User.is_active == is_active)
        # Total count
        count_result = await db.execute(select(User.id).filter(query.whereclause))
        total = len(count_result.scalars().all())
        # Paginated results
        result = await db.execute(
            query.order_by(User.created_at.desc()).offset(offset).limit(limit)
        )
        users = result.scalars().all()
        items = [
            UserListResponse(
                id=u.id,
                email=u.email,
                full_name=u.full_name,
                is_active=u.is_active,
                is_superuser=u.is_superuser,
                last_login_at=u.last_login_at,
                roles=[r.name for r in u.roles],
            )
            for u in users
        ]
        return items, total

    @staticmethod
    async def update_user(
        db: AsyncSession,
        user_id: uuid.UUID,
        data: UserUpdate,
        updated_by: User,
    ) -> UserResponse:
        """Update user fields."""
        result = await db.execute(select(User).filter(User.id == user_id))
        user = result.scalars().first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )
        update_data = data.model_dump(exclude_none=True)
        for field, value in update_data.items():
            setattr(user, field, value)
        await db.commit()
        await db.refresh(user)
        await AuditService.log(
            db,
            AuditAction.USER_UPDATED,
            user_id=updated_by.id,
            entity_type="User",
            entity_id=user.id,
            metadata=update_data,
        )
        return UserResponse.model_validate(user)

    @staticmethod
    async def assign_roles(
        db: AsyncSession,
        user_id: uuid.UUID,
        data: AssignRolesRequest,
        assigned_by: User,
    ) -> UserResponse:
        """Replace a user's roles with the provided set."""
        result = await db.execute(select(User).filter(User.id == user_id))
        user = result.scalars().first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )
        role_result = await db.execute(select(Role).filter(Role.id.in_(data.role_ids)))
        roles = role_result.scalars().all()
        if len(roles) != len(data.role_ids):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="One or more roles not found",
            )
        old_roles = [r.name for r in user.roles]
        user.roles = roles
        await db.commit()
        await db.refresh(user)
        await AuditService.log(
            db,
            AuditAction.ROLE_ASSIGNED,
            user_id=assigned_by.id,
            entity_type="User",
            entity_id=user.id,
            metadata={
                "old_roles": old_roles,
                "new_roles": [r.name for r in roles],
            },
        )
        return UserResponse.model_validate(user)

    @staticmethod
    async def deactivate_user(
        db: AsyncSession,
        user_id: uuid.UUID,
        deactivated_by: User,
    ) -> UserResponse:
        """Deactivate a user account (soft delete)."""
        result = await db.execute(select(User).filter(User.id == user_id))
        user = result.scalars().first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )
        if user.id == deactivated_by.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot deactivate your own account",
            )
        user.is_active = False
        await db.commit()
        await db.refresh(user)
        await AuditService.log(
            db,
            AuditAction.USER_DEACTIVATED,
            user_id=deactivated_by.id,
            entity_type="User",
            entity_id=user.id,
        )
        return UserResponse.model_validate(user)

    # ── Roles & Permissions ────────────────────────────────────────────────────

    @staticmethod
    async def list_roles(db: AsyncSession) -> list[Role]:
        """Return all roles ordered by name."""
        result = await db.execute(select(Role).order_by(Role.name))
        return result.scalars().all()

    @staticmethod
    async def list_permissions(db: AsyncSession) -> list[Permission]:
        """Return all permissions ordered by category then name."""
        result = await db.execute(
            select(Permission).order_by(Permission.category, Permission.name)
        )
        return result.scalars().all()
