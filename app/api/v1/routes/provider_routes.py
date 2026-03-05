"""
SHA Fraud Detection — Provider Routes

GET    /api/v1/providers                List providers (search + filter + paginate)
POST   /api/v1/providers                Register a new provider
GET    /api/v1/providers/{id}           Full provider detail (Provider_single_page.png)
PATCH  /api/v1/providers/{id}           Update provider record
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import PaginationParams, get_db, require_permission
from app.models.enums_model import FacilityType, RiskLevel
from app.models.user_model import User
from app.schemas.base_schema import PaginatedResponse
from app.schemas.provider_schema import (
    ProviderCreate,
    ProviderDetailResponse,
    ProviderListFilter,
    ProviderListItem,
    ProviderResponse,
    ProviderUpdate,
)
from app.services.provider_service import ProviderService

router = APIRouter(tags=["Providers"])


# ── List providers ────────────────────────────────────────────────────────────


@router.get(
    "/providers",
    response_model=PaginatedResponse[ProviderListItem],
    summary="List providers",
    description="""
Returns a paginated list of providers matching the Providers page (Providers_page.png).

Each row includes: Provider name + code, Facility Type, County, Total Claims,
Flagged % and a colour-coded Risk Score pill.

**Filters:**
- `search` — partial match on provider name or SHA code (e.g. `NHF-00001`)
- `county` — e.g. `Nairobi`, `Mombasa`
- `facility_type` — `CLINIC` | `PRIVATE_HOSPITAL` | `PUBLIC_HOSPITAL` | `LABORATORY` | `PHARMACY` | `SPECIALIST_CENTER` | `FAITH_BASED`
- `risk_level` — `LOW` | `MEDIUM` | `HIGH` | `CRITICAL`

**Pagination:** `page` (default 1), `page_size` (default 25).
""",
)
async def list_providers(
    search: Optional[str] = Query(
        None, description="Search by provider name or code", examples="MP Shah"
    ),
    county: Optional[str] = Query(
        None, description="Filter by county", examples="Nairobi"
    ),
    facility_type: Optional[str] = Query(
        None,
        description="Filter by facility type: CLINIC | PRIVATE_HOSPITAL | PUBLIC_HOSPITAL | LABORATORY | PHARMACY | SPECIALIST_CENTER | FAITH_BASED",
    ),
    risk_level: Optional[str] = Query(
        None, description="Filter by risk level: LOW | MEDIUM | HIGH | CRITICAL"
    ),
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("view_claim")),
):
    # Validate enum inputs with clear error messages
    try:
        ft_filter = FacilityType(facility_type) if facility_type else None
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid facility_type '{facility_type}'. "
            f"Valid values: {[f.value for f in FacilityType]}",
        )

    try:
        rl_filter = RiskLevel(risk_level) if risk_level else None
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid risk_level '{risk_level}'. "
            f"Valid values: {[r.value for r in RiskLevel]}",
        )

    filters = ProviderListFilter(
        search=search,
        county=county,
        facility_type=ft_filter,
        risk_level=rl_filter,
    )

    items, total = await ProviderService.list_providers(
        db, filters, offset=pagination.offset, limit=pagination.page_size
    )

    return PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=-(-total // pagination.page_size) if total else 0,
    )


# ── Register provider ─────────────────────────────────────────────────────────


@router.post(
    "/providers",
    response_model=ProviderResponse,
    status_code=201,
    summary="Register a new provider",
    description="Create a new provider record. SHA provider code must be unique.",
)
async def create_provider(
    data: ProviderCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("ingest_claim")),
):
    return await ProviderService.create_provider(db, data, created_by=current_user.id)


# ── Provider detail ───────────────────────────────────────────────────────────


@router.get(
    "/providers/{provider_id}",
    response_model=ProviderDetailResponse,
    summary="Get full provider detail",
    description="""
Returns the full provider detail view (Provider_single_page.png).

**Header stats:** Risk Score pill, Total Claims, Flagged Claims %, Confirmed Fraud count.

**Provider Information:** Facility Type, County, Contact, Email, Bed Capacity, Status.

**Risk Profile:** Three labelled progress bars —
  Claim Deviation (how far the provider's avg claim deviates from peers),
  Rejection Rate, Fraud History Score.

**Quick Stats sidebar:** Total Claims, Flagged, Confirmed Fraud, Last Claim date.

**Fraud History sidebar:** Confirmed Cases, Suspected Cases, Total Amount (KES).

**Statistics:** Total Amount, Average Claim, Rejection Rate, Avg Processing Time (days).

All numbers are computed live from claim and fraud case data — not cached.
""",
)
async def get_provider_detail(
    provider_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("view_claim")),
):
    return await ProviderService.get_provider_detail(db, provider_id)


# ── Update provider ───────────────────────────────────────────────────────────


@router.patch(
    "/providers/{provider_id}",
    response_model=ProviderResponse,
    summary="Update provider record",
    description="Update editable provider fields. Only provided fields are changed (PATCH semantics).",
)
async def update_provider(
    provider_id: uuid.UUID,
    data: ProviderUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("manage_rules")),
):
    return await ProviderService.update_provider(
        db, provider_id, data, updated_by=current_user.id
    )
