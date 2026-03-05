"""
SHA Fraud Detection — FraudReport Model

Stores metadata for every generated report. Report content (key metrics,
summary text, per-type data) is kept in the `report_data` JSONB column so
the table stays schema-stable while report content evolves.

Columns map directly to the three UI screens:

  LIST PAGE (Report_1.png)
  ─────────────────────────────────────────────────────────────────────
  Report Name  │ name
  Type         │ report_type       (Summary / Provider / Investigation / County)
  Period       │ period_label      e.g. "2024-02-05 to 2024-02-11"
  Status       │ status            (completed → green, processing → blue, ...)
  Records      │ record_count
  Generated    │ generated_at
  Actions      │ — download (when completed) / view

  GENERATE DIALOG (Report_2.png)
  ─────────────────────────────────────────────────────────────────────
  Report Name *   │ name
  Report Type *   │ report_type
  Date Range      │ date_range_preset  (week/month/quarter/year/custom)
                  │ period_start / period_end  (for custom or resolved preset)
  Additional Notes│ custom_notes
  generated_by    │ FK → users.id

  VIEW DIALOG (Reports_2.png — detail modal)
  ─────────────────────────────────────────────────────────────────────
  Report Type     │ report_type
  Status          │ status
  Period          │ period_label
  Records Analyzed│ record_count
  Report Summary  │ summary_text  (generated when report completes)
  Key Metrics     │ report_data JSONB:
                  │   fraud_detection_rate   (12.0%)   → blue
                  │   fraud_amount_detected  (63,195)  → green
                  │   alert_cases_generated  (91)      → orange
"""

from __future__ import annotations

import uuid
from datetime import datetime, UTC
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums_model import ReportStatus, ReportType

if TYPE_CHECKING:
    from app.models.user_model import User


class FraudReport(Base):
    """
    One row per generated report.

    report_data stores the computed payload:
    {
      "fraud_detection_rate": 12.0,        # %
      "fraud_amount_detected": 63195.112,  # KES
      "alert_cases_generated": 91,
      "type_specific": { ... }             # per-type breakdown
    }
    """

    __tablename__ = "fraud_reports"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # ── Core display fields ───────────────────────────────────────────────────
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="User-provided report name, e.g. 'Weekly Fraud Summary'",
    )
    report_type: Mapped[ReportType] = mapped_column(
        String(50),
        nullable=False,
        index=True,
        comment="summary | provider | investigation | county",
    )
    status: Mapped[ReportStatus] = mapped_column(
        String(20),
        nullable=False,
        default=ReportStatus.PROCESSING,
        index=True,
        comment="processing | completed | scheduled | failed",
    )
    # ── Period ────────────────────────────────────────────────────────────────
    date_range_preset: Mapped[Optional[str]] = mapped_column(
        String(20), comment="week | month | quarter | year | custom"
    )
    period_start: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), comment="Start of the analysis window"
    )
    period_end: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), comment="End of the analysis window"
    )
    period_label: Mapped[Optional[str]] = mapped_column(
        String(100),
        comment="Human-readable period shown in the table, e.g. '2024-02-05 to 2024-02-11'",
    )
    # ── Content ───────────────────────────────────────────────────────────────
    record_count: Mapped[int] = mapped_column(
        Integer, default=0, comment="Number of claims/records analysed"
    )
    summary_text: Mapped[Optional[str]] = mapped_column(
        Text, comment="Auto-generated narrative shown in the Report Summary card"
    )
    custom_notes: Mapped[Optional[str]] = mapped_column(
        Text, comment="Optional analyst notes entered in the Generate dialog"
    )
    report_data: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        default=dict,
        comment="""
        Computed metrics stored as JSONB:
        {
          "fraud_detection_rate": 12.0,
          "fraud_amount_detected": 63195.112,
          "alert_cases_generated": 91,
          "type_specific": {}
        }
        """,
    )
    download_url: Mapped[Optional[str]] = mapped_column(
        String(500), comment="Storage URL for the generated report file (PDF/CSV/JSON)"
    )
    # ── Timestamps ────────────────────────────────────────────────────────────
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        comment="When generation was triggered (shown in Generated column)",
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), comment="When the report finished processing"
    )
    # ── Ownership ─────────────────────────────────────────────────────────────
    generated_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ── Relationships ─────────────────────────────────────────────────────────

    generator: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[generated_by]
    )

    def __repr__(self) -> str:
        return f"<FraudReport {self.name!r} [{self.report_type}] {self.status}>"
