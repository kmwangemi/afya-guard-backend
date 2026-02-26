"""
SHA Fraud Detection — Audit Service

Writes immutable audit log entries.
Called by every other service after state-changing operations.
"""

import uuid
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log_model import AuditLog
from app.models.enums_model import AuditAction


class AuditService:

    @staticmethod
    async def log(
        db: AsyncSession,
        action: AuditAction,
        *,
        user_id: Optional[uuid.UUID] = None,
        entity_type: Optional[str] = None,
        entity_id: Optional[uuid.UUID] = None,
        metadata: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> AuditLog:
        """
        Write one audit log entry.

        Args:
            db:          Active async DB session.
            action:      AuditAction enum value.
            user_id:     ID of the acting user (None for system actions).
            entity_type: ORM model name, e.g. "Claim", "FraudCase".
            entity_id:   PK of the affected record.
            metadata:    Any extra context dict (diff, old/new values, etc.).
            ip_address:  Request IP (pass from FastAPI Request object).
            user_agent:  Browser/client user-agent string.
        """
        entry = AuditLog(
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            metadata=metadata or {},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        db.add(entry)
        await db.commit()
        await db.refresh(entry)
        return entry

    @staticmethod
    async def get_entity_logs(
        db: AsyncSession,
        entity_type: str,
        entity_id: uuid.UUID,
        limit: int = 50,
    ) -> list[AuditLog]:
        """Retrieve audit history for a specific entity (e.g. all events on a FraudCase)."""
        result = await db.execute(
            select(AuditLog)
            .filter(
                AuditLog.entity_type == entity_type,
                AuditLog.entity_id == entity_id,
            )
            .order_by(AuditLog.performed_at.desc())
            .limit(limit)
        )
        return result.scalars().all()
