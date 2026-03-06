"""
SHA Fraud Detection — Audit Log Routes
"""

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload
from sqlalchemy.sql.functions import count

from app.core.dependencies import PaginationParams, get_db, require_permission
from app.models.audit_log_model import AuditLog
from app.models.enums_model import AuditAction
from app.models.user_model import User
from app.schemas.base_schema import PaginatedResponse
from app.schemas.log_schema import AuditLogResponse

router = APIRouter(tags=["Audit Logs"])


@router.get(
    "/logs",
    response_model=PaginatedResponse[AuditLogResponse],
)
async def list_audit_logs(
    action: Optional[str] = Query(None),
    entity_type: Optional[str] = Query(None),
    user_id: Optional[uuid.UUID] = Query(None),
    from_date: Optional[datetime] = Query(None),
    to_date: Optional[datetime] = Query(None),
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("manage_users")),
):
    # ── Base query ─────────────────────────────────
    query = select(AuditLog).options(joinedload(AuditLog.user))
    # ── Filters ────────────────────────────────────
    if action:
        try:
            query = query.where(AuditLog.action == AuditAction(action))
        except ValueError:
            return PaginatedResponse(
                items=[],
                total=0,
                page=pagination.page,
                page_size=pagination.page_size,
                pages=0,
            )
    if entity_type:
        query = query.where(AuditLog.entity_type == entity_type)
    if user_id:
        query = query.where(AuditLog.user_id == user_id)
    if from_date:
        query = query.where(AuditLog.performed_at >= from_date)
    if to_date:
        query = query.where(AuditLog.performed_at <= to_date)
    # ── Count query ────────────────────────────────
    count_query = select(count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()
    # ── Pagination ─────────────────────────────────
    query = (
        query.order_by(AuditLog.performed_at.desc())
        .offset(pagination.offset)
        .limit(pagination.page_size)
    )
    result = await db.execute(query)
    logs = result.scalars().all()
    # ── Response mapping ───────────────────────────
    items = [
        AuditLogResponse(
            id=str(log.id),
            action=log.action.value,
            entity_type=log.entity_type,
            entity_id=str(log.entity_id) if log.entity_id else None,
            audit_log_metadata=log.audit_log_metadata or {},
            ip_address=log.ip_address,
            user_agent=log.user_agent,
            performed_at=log.performed_at,
            user_id=str(log.user_id) if log.user_id else None,
            user_full_name=log.user.full_name if log.user else None,
            user_email=log.user.email if log.user else None,
        )
        for log in logs
    ]
    return PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=-(-total // pagination.page_size) if total else 0,
    )
