"""
SHA Fraud Detection — User Service

Handles: user CRUD, role assignment, permission management, profile.

Changes vs the version in the document:
  1. create_user   — passes department= to User(...)
  2. list_users    — uses func.count() (no .all()+len()), includes department
  3. get_profile() — new: returns the current user's own full profile
  4. update_profile() — new: self-update of name / phone / department
"""

import uuid
from typing import List, Optional

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.functions import count

from app.core.security import hash_password
from app.models.enums_model import AuditAction, CaseStatus
from app.models.fraud_alert_model import FraudAlert
from app.models.fraud_case_model import FraudCase
from app.models.permission_model import Permission
from app.models.role_model import Role
from app.models.user_model import User
from app.schemas.user_schema import (
    AssignRolesRequest,
    UserCreate,
    UserListResponse,
    UserPerformanceStats,
    UserProfileUpdate,
    UserResponse,
)
from app.services.audit_service import AuditService


def _load_user():
    """select(User) with roles + permissions eager-loaded."""
    return select(User).options(selectinload(User.roles).selectinload(Role.permissions))


class UserService:

    # ── Create ────────────────────────────────────────────────────────────────

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
        roles: List[Role] = []
        if data.role_ids:
            role_result = await db.execute(
                select(Role).filter(Role.id.in_(data.role_ids))
            )
            roles = role_result.scalars().all()
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
            department=data.department,  # ← was missing
            must_change_password=True,
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
        result = await db.execute(_load_user().filter(User.id == user.id))
        return UserResponse.model_validate(result.scalars().first())

    # ── Read ──────────────────────────────────────────────────────────────────

    @staticmethod
    async def get_user(db: AsyncSession, user_id: uuid.UUID) -> UserResponse:
        """Fetch a single user by ID."""
        result = await db.execute(_load_user().filter(User.id == user_id))
        user = result.scalars().first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return UserResponse.model_validate(user)

    @staticmethod
    async def get_profile(db: AsyncSession, current_user: User) -> UserResponse:
        """
        Return the authenticated user's own profile.
        Called by GET /api/v1/users/me — no special permission required.
        """
        result = await db.execute(_load_user().filter(User.id == current_user.id))
        user = result.scalars().first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return UserResponse.model_validate(user)

    # ── List ──────────────────────────────────────────────────────────────────

    @staticmethod
    async def list_users(
        db: AsyncSession,
        is_active: Optional[bool] = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[List[UserListResponse], int]:
        """List users with optional active filter and pagination."""
        q = select(User).options(selectinload(User.roles))
        if is_active is not None:
            q = q.filter(User.is_active == is_active)
        # COUNT using SQL aggregate — never fetches rows just to call len()
        count_result = await db.execute(select(count()).select_from(q.subquery()))
        total = count_result.scalar_one()
        result = await db.execute(
            q.order_by(User.created_at.desc()).offset(offset).limit(limit)
        )
        users = result.scalars().all()
        items = [
            UserListResponse(
                id=u.id,
                email=u.email,
                full_name=u.full_name,
                is_active=u.is_active,
                is_superuser=u.is_superuser,
                department=u.department,  # ← was missing
                last_login_at=u.last_login_at,
                roles=[r.name for r in u.roles],
            )
            for u in users
        ]
        return items, total

    # ── Update ────────────────────────────────────────────────────────────────

    @staticmethod
    async def update_user(
        db: AsyncSession,
        user_id: uuid.UUID,
        data: UserProfileUpdate,
        updated_by: User,
    ) -> UserResponse:
        """Admin update of any user's fields."""
        result = await db.execute(select(User).filter(User.id == user_id))
        user = result.scalars().first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
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
        result = await db.execute(_load_user().filter(User.id == user.id))
        return UserResponse.model_validate(result.scalars().first())

    @staticmethod
    async def update_profile(
        db: AsyncSession,
        current_user: User,
        data: UserProfileUpdate,
    ) -> UserResponse:
        """
        User updates their own profile.
        Scoped to name / phone / department only.
        Called by PATCH /api/v1/users/me.
        """
        result = await db.execute(select(User).filter(User.id == current_user.id))
        user = result.scalars().first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        update_data = data.model_dump(exclude_none=True)
        for field, value in update_data.items():
            setattr(user, field, value)
        await db.commit()
        await db.refresh(user)
        await AuditService.log(
            db,
            AuditAction.USER_UPDATED,
            user_id=current_user.id,
            entity_type="User",
            entity_id=user.id,
            metadata={"self_update": True, **update_data},
        )
        result = await db.execute(_load_user().filter(User.id == user.id))
        return UserResponse.model_validate(result.scalars().first())

    # ── Roles ─────────────────────────────────────────────────────────────────

    @staticmethod
    async def assign_roles(
        db: AsyncSession,
        user_id: uuid.UUID,
        data: AssignRolesRequest,
        assigned_by: User,
    ) -> UserResponse:
        """Replace a user's roles with the provided set."""
        result = await db.execute(_load_user().filter(User.id == user_id))
        user = result.scalars().first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        role_result = await db.execute(select(Role).filter(Role.id.in_(data.role_ids)))
        roles = role_result.scalars().all()
        if len(roles) != len(data.role_ids):
            raise HTTPException(status_code=404, detail="One or more roles not found")
        old_roles = [r.name for r in user.roles]
        user.roles = list(roles)
        await db.commit()
        await db.refresh(user)
        await AuditService.log(
            db,
            AuditAction.ROLE_ASSIGNED,
            user_id=assigned_by.id,
            entity_type="User",
            entity_id=user.id,
            metadata={"old_roles": old_roles, "new_roles": [r.name for r in roles]},
        )
        result = await db.execute(_load_user().filter(User.id == user.id))
        return UserResponse.model_validate(result.scalars().first())

    # ── Deactivate ────────────────────────────────────────────────────────────

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
            raise HTTPException(status_code=404, detail="User not found")
        if user.id == deactivated_by.id:
            raise HTTPException(
                status_code=400,
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
        result = await db.execute(_load_user().filter(User.id == user.id))
        return UserResponse.model_validate(result.scalars().first())

    # ── Performance stats ─────────────────────────────────────────────────────

    @staticmethod
    async def get_performance_stats(
        db: AsyncSession,
        current_user: User,
    ) -> UserPerformanceStats:
        """
        Four Performance Statistics tiles (Performance_stats.png):

          Cases Investigated    — FraudCase.assigned_to = me (any status)
          Alerts Reviewed       — FraudAlert.assigned_to = me (any status)
          Fraud Cases Confirmed — FraudCase assigned to me + CONFIRMED_FRAUD
          Total Fraud Recovered — SUM(estimated_loss) on confirmed cases assigned to me
        """
        uid = current_user.id
        cases_res = await db.execute(
            select(count(FraudCase.id)).filter(FraudCase.assigned_to == uid)
        )
        cases_investigated = cases_res.scalar_one() or 0
        alerts_res = await db.execute(
            select(count(FraudAlert.id)).filter(FraudAlert.assigned_to == uid)
        )
        alerts_reviewed = alerts_res.scalar_one() or 0
        confirmed_res = await db.execute(
            select(count(FraudCase.id)).filter(
                FraudCase.assigned_to == uid,
                FraudCase.status == CaseStatus.CONFIRMED_FRAUD,
            )
        )
        fraud_cases_confirmed = confirmed_res.scalar_one() or 0
        amount_res = await db.execute(
            select(func.coalesce(func.sum(FraudCase.estimated_loss), 0)).filter(
                FraudCase.assigned_to == uid,
                FraudCase.status == CaseStatus.CONFIRMED_FRAUD,
                FraudCase.estimated_loss.isnot(None),
            )
        )
        total_amount = float(amount_res.scalar_one() or 0)
        # Format "KES 2.4M" / "KES 850.0K" / "KES 320" — matches UI tile
        if total_amount >= 1_000_000:
            display = f"KES {total_amount / 1_000_000:.1f}M"
        elif total_amount >= 1_000:
            display = f"KES {total_amount / 1_000:.1f}K"
        else:
            display = f"KES {total_amount:,.0f}"
        return UserPerformanceStats(
            cases_investigated=cases_investigated,
            alerts_reviewed=alerts_reviewed,
            fraud_cases_confirmed=fraud_cases_confirmed,
            total_fraud_amount_recovered=total_amount,
            total_fraud_amount_display=display,
        )

        # ── Roles & Permissions ────────────────────────────────────────────────────

    @staticmethod
    async def list_roles(db: AsyncSession) -> List[Role]:
        """Return all roles ordered by name."""
        result = await db.execute(
            select(Role).options(selectinload(Role.permissions)).order_by(Role.name)
        )
        return result.scalars().all()

    @staticmethod
    async def list_permissions(db: AsyncSession) -> List[Permission]:
        """Return all permissions ordered by category then name."""
        result = await db.execute(
            select(Permission).order_by(Permission.category, Permission.name)
        )
        return result.scalars().all()
