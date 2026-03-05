"""
SHA Fraud Detection — Investigation Service

Fully aligned to the investigation UI screenshots:
  investigation-page.png         → list_investigations()
  investigation_single_page_*.png→ get_investigation_detail()

INV number format: "INV-00084" (derived from UUID, stable)

New fields added to FraudCase model (require migration):
  progress    INTEGER DEFAULT 0
  findings    TEXT
  target_date TIMESTAMP
  evidence    JSONB DEFAULT '[]'
"""

import uuid
from datetime import UTC, date, datetime
from typing import List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.sql.functions import count
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.case_note_model import CaseNote
from app.models.claim_model import Claim
from app.models.enums_model import AuditAction, CaseStatus
from app.models.fraud_alert_model import FraudAlert
from app.models.fraud_case_model import FraudCase
from app.models.fraud_score_model import FraudScore
from app.models.provider_model import Provider
from app.models.user_model import User
from app.schemas.case_schema import (
    CaseNoteCreate,
    CaseNoteResponse,
    EvidenceFile,
    EvidenceUpload,
    InvestigationAssignRequest,
    InvestigationCreate,
    InvestigationDetailResponse,
    InvestigationDetails,
    InvestigationListFilter,
    InvestigationListItem,
    InvestigationProgressUpdate,
    InvestigationQuickActions,
    InvestigationStatCards,
    InvestigationStatusUpdate,
    InvestigationSummary,
    TimelineEvent,
)
from app.services.audit_service import AuditService

# ── Display helpers ────────────────────────────────────────────────────────────


def _inv_number(case: FraudCase) -> str:
    """
    Format the sequential case_number column as "INV-00084".
    case_number is an auto-incrementing INTEGER on fraud_cases —
    guaranteed unique and sequential, never derived from the UUID.
    """
    return f"INV-{case.case_number:05d}"


_STATUS_TRANSITIONS: dict[CaseStatus, list[CaseStatus]] = {
    CaseStatus.OPEN: [
        CaseStatus.UNDER_REVIEW,
        CaseStatus.CONFIRMED_FRAUD,
        CaseStatus.CLEARED,
        CaseStatus.CLOSED,
    ],
    CaseStatus.UNDER_REVIEW: [
        CaseStatus.CONFIRMED_FRAUD,
        CaseStatus.CLEARED,
        CaseStatus.CLOSED,
    ],
    CaseStatus.CONFIRMED_FRAUD: [CaseStatus.CLOSED],
    CaseStatus.CLEARED: [CaseStatus.CLOSED],
    CaseStatus.CLOSED: [],
}

_TERMINAL = {CaseStatus.CONFIRMED_FRAUD, CaseStatus.CLEARED, CaseStatus.CLOSED}


def _load_case():
    """select(FraudCase) with all relationships eager-loaded."""
    return select(FraudCase).options(
        selectinload(FraudCase.claim).selectinload(Claim.provider),
        selectinload(FraudCase.claim).selectinload(Claim.member),
        selectinload(FraudCase.fraud_score),
        selectinload(FraudCase.assigned_analyst),
        selectinload(FraudCase.notes).selectinload(CaseNote.author),
    )


def _days_open(case: FraudCase) -> int:
    end = case.closed_at or datetime.now(UTC)
    opened = case.opened_at
    # Normalise timezone awareness
    if opened.tzinfo is None:
        opened = opened.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    return max(0, (end - opened).days)


def _build_timeline(
    case: FraudCase, alert: Optional[FraudAlert] = None
) -> List[TimelineEvent]:
    events: List[TimelineEvent] = []

    # ① Alert created (from linked FraudAlert if available)
    if alert:
        events.append(
            TimelineEvent(
                event="Alert created",
                actor="System",
                note="Automatic alert generated",
                timestamp=alert.raised_at,
            )
        )

    # ② Investigation opened
    events.append(
        TimelineEvent(
            event="Investigation opened",
            actor=(
                case.assigned_analyst.full_name if case.assigned_analyst else "System"
            ),
            note="Case assigned for investigation",
            timestamp=case.opened_at,
        )
    )

    # ③ Status changes derived from closed_at
    if case.status == CaseStatus.UNDER_REVIEW:
        events.append(
            TimelineEvent(
                event="Under review",
                actor=(
                    case.assigned_analyst.full_name
                    if case.assigned_analyst
                    else "System"
                ),
                note=None,
                timestamp=case.opened_at,
            )
        )

    if case.closed_at:
        label_map = {
            CaseStatus.CONFIRMED_FRAUD: "Fraud confirmed",
            CaseStatus.CLEARED: "Case cleared — no fraud",
            CaseStatus.CLOSED: "Investigation closed",
        }
        events.append(
            TimelineEvent(
                event=label_map.get(case.status, "Investigation closed"),
                actor=(
                    case.assigned_analyst.full_name
                    if case.assigned_analyst
                    else "System"
                ),
                note=case.resolution_summary,
                timestamp=case.closed_at,
            )
        )

    # ④ Analyst note additions
    for note in sorted(case.notes or [], key=lambda n: n.created_at):
        events.append(
            TimelineEvent(
                event="Note added",
                actor=note.author.full_name if note.author else "Analyst",
                note=note.note[:100] + "…" if len(note.note) > 100 else note.note,
                timestamp=note.created_at,
            )
        )

    return sorted(events, key=lambda e: e.timestamp)


def _build_evidence(case: FraudCase) -> List[EvidenceFile]:
    raw = case.evidence or []
    result = []
    for item in raw:
        uploaded_at = None
        if item.get("uploaded_at"):
            try:
                uploaded_at = datetime.fromisoformat(item["uploaded_at"])
            except (ValueError, TypeError):
                pass
        result.append(
            EvidenceFile(
                id=str(item.get("id", "")),
                file_name=item.get("file_name", ""),
                file_type=item.get("file_type", "").upper(),
                file_url=item.get("file_url"),
                uploaded_by=item.get("uploaded_by"),
                uploaded_at=uploaded_at,
            )
        )
    return result


def _build_summary(
    case: FraudCase,
    alert: Optional[FraudAlert] = None,
) -> InvestigationSummary:
    claim = case.claim
    provider = claim.provider if claim else None
    analyst = case.assigned_analyst

    alert_number: Optional[str] = None
    alert_id: Optional[uuid.UUID] = None
    if alert:
        meta = alert.metadata or {}
        alert_number = meta.get("alert_number") or (
            f"ALERT-{int(str(alert.id).replace('-','')[:8], 16) % 100000:05d}"
        )
        alert_id = alert.id

    return InvestigationSummary(
        alert_number=alert_number,
        alert_id=alert_id,
        claim_number=claim.sha_claim_id if claim else None,
        claim_id=claim.id if claim else None,
        provider_name=provider.name if provider else None,
        provider_id=provider.id if provider else None,
        investigator_name=analyst.full_name if analyst else None,
        investigator_id=analyst.id if analyst else None,
    )


async def _fetch_linked_alert(
    db: AsyncSession, case: FraudCase
) -> Optional[FraudAlert]:
    """Fetch the FraudAlert that references this case (if any)."""
    result = await db.execute(
        select(FraudAlert).filter(FraudAlert.fraud_case_id == case.id).limit(1)
    )
    return result.scalars().first()


def _build_detail(
    case: FraudCase, alert: Optional[FraudAlert] = None
) -> InvestigationDetailResponse:
    claim = case.claim
    provider = claim.provider if claim else None
    analyst = case.assigned_analyst

    target_date: Optional[date] = None
    if case.target_date:
        target_date = (
            case.target_date.date()
            if isinstance(case.target_date, datetime)
            else case.target_date
        )

    notes = [
        CaseNoteResponse(
            id=n.id,
            case_id=n.case_id,
            note=n.note,
            attachments=n.attachments,
            created_at=n.created_at,
            author_name=n.author.full_name if n.author else None,
            author_id=n.created_by,
        )
        for n in sorted(case.notes or [], key=lambda n: n.created_at, reverse=True)
    ]

    progress = getattr(case, "progress", 0) or 0
    findings = getattr(case, "findings", None)

    return InvestigationDetailResponse(
        id=case.id,
        inv_number=_inv_number(case),
        subtitle=provider.name if provider else "—",
        stat_cards=InvestigationStatCards(
            status=case.status,
            priority=case.priority,
            days_open=_days_open(case),
            progress=progress,
        ),
        investigation_details=InvestigationDetails(
            investigator_name=analyst.full_name if analyst else None,
            investigator_id=analyst.id if analyst else None,
            related_claim=claim.sha_claim_id if claim else None,
            claim_id=claim.id if claim else None,
            created_at=case.opened_at,
            target_date=target_date,
            closed_at=case.closed_at,
        ),
        findings=findings,
        timeline=_build_timeline(case, alert=alert),
        evidence=_build_evidence(case),
        summary=_build_summary(case, alert=alert),
        notes=notes,
        quick_actions=InvestigationQuickActions(
            available_status_transitions=_STATUS_TRANSITIONS.get(case.status, []),
            can_close=case.status not in _TERMINAL,
            can_update_progress=case.status not in _TERMINAL,
            can_assign=case.status not in _TERMINAL,
            can_upload_evidence=True,
        ),
        status=case.status,
        priority=case.priority,
        progress=progress,
        opened_at=case.opened_at,
        closed_at=case.closed_at,
        claim_id=case.claim_id,
        fraud_score_id=case.fraud_score_id,
        assigned_analyst_id=case.assigned_to,
        resolution_summary=case.resolution_summary,
        estimated_loss=float(case.estimated_loss) if case.estimated_loss else None,
    )


# ══════════════════════════════════════════════════════════════════════════════
# SERVICE
# ══════════════════════════════════════════════════════════════════════════════


class InvestigationService:

    # ── Create ────────────────────────────────────────────────────────────────

    @staticmethod
    async def create(
        db: AsyncSession,
        data: InvestigationCreate,
        created_by: User,
    ) -> InvestigationDetailResponse:
        # Claim exists?
        claim_res = await db.execute(select(Claim).filter(Claim.id == data.claim_id))
        if not claim_res.scalars().first():
            raise HTTPException(status_code=404, detail="Claim not found")

        # One case per claim
        existing = await db.execute(
            select(FraudCase).filter(FraudCase.claim_id == data.claim_id)
        )
        if existing.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An investigation already exists for this claim",
            )

        score_res = await db.execute(
            select(FraudScore).filter(FraudScore.id == data.fraud_score_id)
        )
        if not score_res.scalars().first():
            raise HTTPException(status_code=404, detail="Fraud score not found")

        target_dt: Optional[datetime] = None
        if data.target_date:
            target_dt = datetime.combine(data.target_date, datetime.min.time())

        case = FraudCase(
            claim_id=data.claim_id,
            fraud_score_id=data.fraud_score_id,
            status=CaseStatus.OPEN,
            priority=data.priority,
            assigned_to=data.assigned_to,
            target_date=target_dt,
            progress=0,
            evidence=[],
        )
        db.add(case)
        await db.flush()

        if data.notes:
            db.add(
                CaseNote(
                    case_id=case.id,
                    created_by=created_by.id,
                    note=data.notes,
                )
            )

        await db.commit()

        await AuditService.log(
            db,
            AuditAction.CASE_CREATED,
            user_id=created_by.id,
            entity_type="FraudCase",
            entity_id=case.id,
            metadata={"claim_id": str(data.claim_id), "priority": data.priority},
        )

        result = await db.execute(_load_case().filter(FraudCase.id == case.id))
        case = result.scalars().first()
        alert = await _fetch_linked_alert(db, case)
        return _build_detail(case, alert=alert)

    # ── List  (investigation-page.png) ────────────────────────────────────────

    @staticmethod
    async def list_investigations(
        db: AsyncSession,
        filters: InvestigationListFilter,
        offset: int = 0,
        limit: int = 25,
    ) -> Tuple[List[InvestigationListItem], int]:
        q = (
            select(FraudCase)
            .join(Claim, FraudCase.claim_id == Claim.id, isouter=True)
            .join(Provider, Claim.provider_id == Provider.id, isouter=True)
            .options(
                selectinload(FraudCase.claim).selectinload(Claim.provider),
                selectinload(FraudCase.fraud_score),
                selectinload(FraudCase.assigned_analyst),
                selectinload(FraudCase.notes),
            )
        )

        # Search — INV number (derived), claim number, or provider name
        if filters.search:
            term = f"%{filters.search.strip()}%"
            q = q.filter(
                or_(
                    Claim.sha_claim_id.ilike(term),
                    Provider.name.ilike(term),
                )
            )

        if filters.status:
            q = q.filter(FraudCase.status == filters.status)
        if filters.priority:
            q = q.filter(FraudCase.priority == filters.priority)
        if filters.assigned_to:
            q = q.filter(FraudCase.assigned_to == filters.assigned_to)
        if filters.opened_from:
            q = q.filter(FraudCase.opened_at >= filters.opened_from)
        if filters.opened_to:
            q = q.filter(FraudCase.opened_at <= filters.opened_to)
        if filters.risk_level:
            q = q.join(FraudScore, FraudCase.fraud_score_id == FraudScore.id).filter(
                FraudScore.risk_level == filters.risk_level
            )

        count_result = await db.execute(select(count()).select_from(q.subquery()))
        total = count_result.scalar_one()

        result = await db.execute(
            q.order_by(FraudCase.opened_at.desc()).offset(offset).limit(limit)
        )
        cases = result.scalars().all()

        items = []
        for case in cases:
            items.append(
                InvestigationListItem(
                    id=case.id,
                    inv_number=_inv_number(case),
                    investigator_name=(
                        case.assigned_analyst.full_name
                        if case.assigned_analyst
                        else None
                    ),
                    investigator_id=case.assigned_to,
                    provider_name=(
                        case.claim.provider.name
                        if case.claim and case.claim.provider
                        else None
                    ),
                    provider_id=(
                        case.claim.provider.id
                        if case.claim and case.claim.provider
                        else None
                    ),
                    claim_id=case.claim_id,
                    sha_claim_id=case.claim.sha_claim_id if case.claim else None,
                    status=case.status,
                    priority=case.priority,
                    progress=getattr(case, "progress", 0) or 0,
                    opened_at=case.opened_at,
                    closed_at=case.closed_at,
                    risk_level=(
                        case.fraud_score.risk_level if case.fraud_score else None
                    ),
                    final_score=(
                        float(case.fraud_score.final_score)
                        if case.fraud_score and case.fraud_score.final_score
                        else None
                    ),
                    note_count=len(case.notes or []),
                )
            )

        return items, total

    # ── Detail  (investigation_single_page_*.png) ─────────────────────────────

    @staticmethod
    async def get_detail(
        db: AsyncSession, case_id: uuid.UUID
    ) -> InvestigationDetailResponse:
        result = await db.execute(_load_case().filter(FraudCase.id == case_id))
        case = result.scalars().first()
        if not case:
            raise HTTPException(status_code=404, detail="Investigation not found")
        alert = await _fetch_linked_alert(db, case)
        return _build_detail(case, alert=alert)

    # ── Update Status ─────────────────────────────────────────────────────────

    @staticmethod
    async def update_status(
        db: AsyncSession,
        case_id: uuid.UUID,
        data: InvestigationStatusUpdate,
        updated_by: User,
    ) -> InvestigationDetailResponse:
        result = await db.execute(_load_case().filter(FraudCase.id == case_id))
        case = result.scalars().first()
        if not case:
            raise HTTPException(status_code=404, detail="Investigation not found")

        allowed = _STATUS_TRANSITIONS.get(case.status, [])
        if data.status not in allowed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Cannot move {case.status.value} → {data.status.value}. "
                    f"Allowed: {[s.value for s in allowed]}"
                ),
            )

        if data.status in _TERMINAL and not data.resolution_summary:
            raise HTTPException(
                status_code=400,
                detail="resolution_summary is required when closing or confirming a case",
            )

        case.status = data.status
        if data.resolution_summary:
            case.resolution_summary = data.resolution_summary
        if data.estimated_loss is not None:
            case.estimated_loss = data.estimated_loss
        if data.status in _TERMINAL:
            case.closed_at = datetime.now(UTC)
            if not getattr(case, "progress", None) or case.progress < 100:
                case.progress = 100

        await db.commit()

        await AuditService.log(
            db,
            AuditAction.CASE_STATUS_UPDATED,
            user_id=updated_by.id,
            entity_type="FraudCase",
            entity_id=case.id,
            metadata={"new_status": data.status},
        )

        result = await db.execute(_load_case().filter(FraudCase.id == case_id))
        case = result.scalars().first()
        alert = await _fetch_linked_alert(db, case)
        return _build_detail(case, alert=alert)

    # ── Update Progress ───────────────────────────────────────────────────────

    @staticmethod
    async def update_progress(
        db: AsyncSession,
        case_id: uuid.UUID,
        data: InvestigationProgressUpdate,
        updated_by: User,
    ) -> InvestigationDetailResponse:
        result = await db.execute(select(FraudCase).filter(FraudCase.id == case_id))
        case = result.scalars().first()
        if not case:
            raise HTTPException(status_code=404, detail="Investigation not found")

        case.progress = data.progress
        if data.findings is not None:
            case.findings = data.findings

        await db.commit()

        result = await db.execute(_load_case().filter(FraudCase.id == case_id))
        case = result.scalars().first()
        alert = await _fetch_linked_alert(db, case)
        return _build_detail(case, alert=alert)

    # ── Assign analyst ────────────────────────────────────────────────────────

    @staticmethod
    async def assign(
        db: AsyncSession,
        case_id: uuid.UUID,
        data: InvestigationAssignRequest,
        assigned_by: User,
    ) -> InvestigationDetailResponse:
        result = await db.execute(_load_case().filter(FraudCase.id == case_id))
        case = result.scalars().first()
        if not case:
            raise HTTPException(status_code=404, detail="Investigation not found")

        analyst_res = await db.execute(select(User).filter(User.id == data.assigned_to))
        analyst = analyst_res.scalars().first()
        if not analyst:
            raise HTTPException(status_code=404, detail="Analyst not found")

        case.assigned_to = data.assigned_to
        if case.status == CaseStatus.OPEN:
            case.status = CaseStatus.UNDER_REVIEW

        await db.commit()

        await AuditService.log(
            db,
            AuditAction.CASE_ASSIGNED,
            user_id=assigned_by.id,
            entity_type="FraudCase",
            entity_id=case.id,
            metadata={"analyst": analyst.full_name},
        )

        result = await db.execute(_load_case().filter(FraudCase.id == case_id))
        case = result.scalars().first()
        alert = await _fetch_linked_alert(db, case)
        return _build_detail(case, alert=alert)

    # ── Upload evidence ───────────────────────────────────────────────────────

    @staticmethod
    async def upload_evidence(
        db: AsyncSession,
        case_id: uuid.UUID,
        data: EvidenceUpload,
        uploaded_by: User,
    ) -> InvestigationDetailResponse:
        result = await db.execute(select(FraudCase).filter(FraudCase.id == case_id))
        case = result.scalars().first()
        if not case:
            raise HTTPException(status_code=404, detail="Investigation not found")

        evidence_list = list(case.evidence or [])
        evidence_list.append(
            {
                "id": str(uuid.uuid4()),
                "file_name": data.file_name,
                "file_type": data.file_type,
                "file_url": data.file_url,
                "uploaded_by": uploaded_by.full_name,
                "uploaded_at": datetime.now(UTC).isoformat(),
            }
        )
        case.evidence = evidence_list
        await db.commit()

        result = await db.execute(_load_case().filter(FraudCase.id == case_id))
        case = result.scalars().first()
        alert = await _fetch_linked_alert(db, case)
        return _build_detail(case, alert=alert)

    # ── Add note ──────────────────────────────────────────────────────────────

    @staticmethod
    async def add_note(
        db: AsyncSession,
        case_id: uuid.UUID,
        data: CaseNoteCreate,
        created_by: User,
    ) -> CaseNoteResponse:
        result = await db.execute(select(FraudCase).filter(FraudCase.id == case_id))
        if not result.scalars().first():
            raise HTTPException(status_code=404, detail="Investigation not found")

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
            author_id=note.created_by,
        )
