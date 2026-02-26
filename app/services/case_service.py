"""
SHA Fraud Detection — Case Management Service

Handles full lifecycle of fraud investigation cases:
create, assign, update status, add notes, close.
"""

import uuid
from datetime import UTC, datetime
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case_note_model import CaseNote
from app.models.claim_model import Claim
from app.models.enums_model import AuditAction, CaseStatus
from app.models.fraud_case_model import FraudCase
from app.models.fraud_score_model import FraudScore
from app.models.user_model import User
from app.schemas.case_schema import (
    CaseAssignRequest,
    CaseListFilter,
    CaseNoteCreate,
    CaseNoteResponse,
    CaseStatusUpdate,
    FraudCaseCreate,
    FraudCaseListResponse,
    FraudCaseResponse,
)
from app.services.audit_service import AuditService


class CaseService:

    @staticmethod
    async def create_case(
        db: AsyncSession,
        data: FraudCaseCreate,
        created_by: User,
    ) -> FraudCaseResponse:
        """Manually open a fraud case (system auto-opens for HIGH/CRITICAL)."""
        claim_result = await db.execute(select(Claim).filter(Claim.id == data.claim_id))
        if not claim_result.scalars().first():
            raise HTTPException(status_code=404, detail="Claim not found")
        existing_result = await db.execute(
            select(FraudCase).filter(FraudCase.claim_id == data.claim_id)
        )
        if existing_result.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A fraud case already exists for this claim",
            )
        score_result = await db.execute(
            select(FraudScore).filter(FraudScore.id == data.fraud_score_id)
        )
        fraud_score = score_result.scalars().first()
        if not fraud_score:
            raise HTTPException(status_code=404, detail="Fraud score not found")
        case = FraudCase(
            claim_id=data.claim_id,
            fraud_score_id=data.fraud_score_id,
            status=CaseStatus.OPEN,
            priority=data.priority,
            assigned_to=data.assigned_to,
        )
        db.add(case)
        await db.commit()
        await db.refresh(case)
        await AuditService.log(
            db,
            AuditAction.CASE_CREATED,
            user_id=created_by.id,
            entity_type="FraudCase",
            entity_id=case.id,
            metadata={"claim_id": str(data.claim_id), "priority": data.priority},
        )
        return CaseService._to_response(case, case.claim, fraud_score)

    @staticmethod
    async def get_case(
        db: AsyncSession,
        case_id: uuid.UUID,
    ) -> FraudCaseResponse:
        result = await db.execute(select(FraudCase).filter(FraudCase.id == case_id))
        case = result.scalars().first()
        if not case:
            raise HTTPException(status_code=404, detail="Fraud case not found")
        return CaseService._to_response(case, case.claim, case.fraud_score)

    @staticmethod
    async def list_cases(
        db: AsyncSession,
        filters: CaseListFilter,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[FraudCaseListResponse], int]:
        query = select(FraudCase)
        if filters.status:
            query = query.filter(FraudCase.status == filters.status)
        if filters.priority:
            query = query.filter(FraudCase.priority == filters.priority)
        if filters.assigned_to:
            query = query.filter(FraudCase.assigned_to == filters.assigned_to)
        if filters.opened_from:
            query = query.filter(FraudCase.opened_at >= filters.opened_from)
        if filters.opened_to:
            query = query.filter(FraudCase.opened_at <= filters.opened_to)
        # Total count
        all_result = await db.execute(query)
        all_cases = all_result.scalars().all()
        total = len(all_cases)
        # Paginated results
        paged_result = await db.execute(
            query.order_by(FraudCase.opened_at.desc()).offset(offset).limit(limit)
        )
        cases = paged_result.scalars().all()
        items = []
        for case in cases:
            notes_result = await db.execute(
                select(CaseNote).filter(CaseNote.case_id == case.id)
            )
            note_count = len(notes_result.scalars().all())
            items.append(
                FraudCaseListResponse(
                    id=case.id,
                    claim_id=case.claim_id,
                    sha_claim_id=case.claim.sha_claim_id if case.claim else None,
                    status=case.status,
                    priority=case.priority,
                    risk_level=(
                        case.fraud_score.risk_level if case.fraud_score else None
                    ),
                    final_score=(
                        float(case.fraud_score.final_score)
                        if case.fraud_score and case.fraud_score.final_score
                        else None
                    ),
                    provider_name=(
                        case.claim.provider.name
                        if case.claim and case.claim.provider
                        else None
                    ),
                    assigned_analyst_name=(
                        case.assigned_analyst.full_name
                        if case.assigned_analyst
                        else None
                    ),
                    opened_at=case.opened_at,
                    closed_at=case.closed_at,
                    note_count=note_count,
                )
            )
        return items, total

    @staticmethod
    async def assign_case(
        db: AsyncSession,
        case_id: uuid.UUID,
        data: CaseAssignRequest,
        assigned_by: User,
    ) -> FraudCaseResponse:
        case_result = await db.execute(
            select(FraudCase).filter(FraudCase.id == case_id)
        )
        case = case_result.scalars().first()
        if not case:
            raise HTTPException(status_code=404, detail="Fraud case not found")
        analyst_result = await db.execute(
            select(User).filter(User.id == data.assigned_to)
        )
        analyst = analyst_result.scalars().first()
        if not analyst:
            raise HTTPException(status_code=404, detail="Analyst user not found")
        old_assignee = case.assigned_to
        case.assigned_to = data.assigned_to
        await db.commit()
        await db.refresh(case)
        await AuditService.log(
            db,
            AuditAction.CASE_ASSIGNED,
            user_id=assigned_by.id,
            entity_type="FraudCase",
            entity_id=case.id,
            metadata={
                "old_assignee": str(old_assignee) if old_assignee else None,
                "new_assignee": str(data.assigned_to),
                "analyst_name": analyst.full_name,
            },
        )
        return CaseService._to_response(case, case.claim, case.fraud_score)

    @staticmethod
    async def update_status(
        db: AsyncSession,
        case_id: uuid.UUID,
        data: CaseStatusUpdate,
        updated_by: User,
    ) -> FraudCaseResponse:
        result = await db.execute(select(FraudCase).filter(FraudCase.id == case_id))
        case = result.scalars().first()
        if not case:
            raise HTTPException(status_code=404, detail="Fraud case not found")
        terminal_statuses = {
            CaseStatus.CONFIRMED_FRAUD,
            CaseStatus.CLEARED,
            CaseStatus.CLOSED,
        }
        if data.status in terminal_statuses and not data.resolution_summary:
            raise HTTPException(
                status_code=400,
                detail="resolution_summary is required when closing or confirming a case",
            )
        old_status = case.status
        case.status = data.status
        if data.resolution_summary:
            case.resolution_summary = data.resolution_summary
        if data.estimated_loss is not None:
            case.estimated_loss = data.estimated_loss
        if data.status in terminal_statuses:
            case.closed_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(case)
        await AuditService.log(
            db,
            AuditAction.CASE_STATUS_UPDATED,
            user_id=updated_by.id,
            entity_type="FraudCase",
            entity_id=case.id,
            metadata={"old_status": old_status, "new_status": data.status},
        )
        return CaseService._to_response(case, case.claim, case.fraud_score)

    @staticmethod
    async def add_note(
        db: AsyncSession,
        case_id: uuid.UUID,
        data: CaseNoteCreate,
        created_by: User,
    ) -> CaseNoteResponse:
        result = await db.execute(select(FraudCase).filter(FraudCase.id == case_id))
        if not result.scalars().first():
            raise HTTPException(status_code=404, detail="Fraud case not found")
        note = CaseNote(
            case_id=case_id,
            created_by=created_by.id,
            note=data.note,
            attachments=data.attachments,
        )
        db.add(note)
        await db.commit()
        await db.refresh(note)
        await AuditService.log(
            db,
            AuditAction.CASE_NOTE_ADDED,
            user_id=created_by.id,
            entity_type="CaseNote",
            entity_id=note.id,
            metadata={"case_id": str(case_id)},
        )
        return CaseNoteResponse(
            id=note.id,
            case_id=note.case_id,
            note=note.note,
            attachments=note.attachments,
            created_at=note.created_at,
            author_name=created_by.full_name,
        )

    # ── Helper ────────────────────────────────────────────────────────────────

    @staticmethod
    def _to_response(
        case: FraudCase,
        claim: Optional[Claim],
        fraud_score: Optional[FraudScore],
    ) -> FraudCaseResponse:
        notes = [
            CaseNoteResponse(
                id=n.id,
                case_id=n.case_id,
                note=n.note,
                attachments=n.attachments,
                created_at=n.created_at,
                author_name=n.author.full_name if n.author else None,
            )
            for n in (case.notes or [])
        ]
        return FraudCaseResponse(
            id=case.id,
            claim_id=case.claim_id,
            fraud_score_id=case.fraud_score_id,
            status=case.status,
            priority=case.priority,
            assigned_to=case.assigned_to,
            assigned_analyst_name=(
                case.assigned_analyst.full_name if case.assigned_analyst else None
            ),
            resolution_summary=case.resolution_summary,
            estimated_loss=float(case.estimated_loss) if case.estimated_loss else None,
            opened_at=case.opened_at,
            closed_at=case.closed_at,
            notes=notes,
            sha_claim_id=claim.sha_claim_id if claim else None,
            provider_name=claim.provider.name if claim and claim.provider else None,
            final_score=(
                float(fraud_score.final_score)
                if fraud_score and fraud_score.final_score
                else None
            ),
            risk_level=fraud_score.risk_level if fraud_score else None,
        )
