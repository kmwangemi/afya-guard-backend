"""
SHA Fraud Detection — Report Schemas

Shaped to match all three UI screens exactly:

  LIST PAGE  (Report_1.png)
    4 stat cards:  Total Reports | Completed (green) | Processing (purple) | Total Records
    Filters:       Search reports... | All Types dropdown
    Table columns: Report Name | Type | Period | Status badge | Records | Generated | Actions

  GENERATE DIALOG  (Report_2.png / Report_3.png)
    Fields: Report Name * | Report Type * | Date Range | Additional Notes
    Note banner: "Report generation typically takes 30 seconds to 2 minutes..."

  VIEW DIALOG  (Report_1.png — modal)
    4 info cards:  Report Type | Status | Period | Records Analyzed
    Report Summary card:  narrative text
    Key Metrics card:
      Fraud Detection Rate  (blue  %)
      Fraud Amount Detected (green KES)
      Alert Cases Generated (orange int)
    Footer: Close | Download Report (disabled if not completed)
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import Field

from app.models.enums_model import DateRangePreset, ReportStatus, ReportType
from app.schemas.base_schema import BaseSchema

# ══════════════════════════════════════════════════════════════════════════════
# WRITE SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════


class ReportGenerateRequest(BaseSchema):
    """
    Payload for the 'Generate New Report' dialog (Report_2.png / Report_3.png).

    Required:  name, report_type
    Optional:  date_range_preset (defaults to month), custom_notes
               period_start / period_end only needed when preset = 'custom'
    """

    name: str = Field(
        min_length=1,
        max_length=255,
        description="Report name — e.g. 'Monthly Fraud Analysis'",
    )
    report_type: ReportType = Field(
        description="summary | provider | investigation | county"
    )
    date_range_preset: DateRangePreset = Field(
        default=DateRangePreset.MONTH,
        description="Preset date range — week | month | quarter | year | custom",
    )
    period_start: Optional[datetime] = Field(
        None,
        description="Required when date_range_preset = 'custom'",
    )
    period_end: Optional[datetime] = Field(
        None,
        description="Required when date_range_preset = 'custom'",
    )
    custom_notes: Optional[str] = Field(
        None,
        description="Additional parameters or notes entered in the dialog",
    )


# ══════════════════════════════════════════════════════════════════════════════
# LIST PAGE SCHEMAS  (Report_1.png)
# ══════════════════════════════════════════════════════════════════════════════


class ReportListFilter(BaseSchema):
    """Filter controls on the Reports list page."""

    search: Optional[str] = Field(
        None,
        description="Search by report name",
    )
    report_type: Optional[ReportType] = Field(
        None,
        description="Filter by type: summary | provider | investigation | county",
    )
    status: Optional[ReportStatus] = None


class ReportListItem(BaseSchema):
    """
    One row in the Reports table.

    Report Name | Type | Period | Status badge | Records | Generated | Actions
    """

    id: uuid.UUID
    # Columns
    name: str  # "Weekly Fraud Summary"
    report_type: ReportType  # "summary" → displayed as "Summary"
    period_label: Optional[str] = None  # "2024-02-05 to 2024-02-11"
    status: ReportStatus  # completed | processing | scheduled | failed
    record_count: int = 0  # 1,250
    # Generated column
    generated_at: datetime  # "3 Mar 2026, 08:38"
    generated_by_name: Optional[str] = None
    # Actions
    can_download: bool = False  # True only when status == completed
    download_url: Optional[str] = None


class ReportListStats(BaseSchema):
    """
    Four stat cards at the top of the Reports page (Report_1.png).

    Total Reports | Completed (green) | Processing (purple) | Total Records
    """

    total_reports: int = 0
    completed: int = 0  # green number
    processing: int = 0  # purple number
    total_records: int = 0  # sum of record_count across all reports


# ══════════════════════════════════════════════════════════════════════════════
# VIEW DIALOG SCHEMAS  (Report_1.png — modal)
# ══════════════════════════════════════════════════════════════════════════════


class ReportKeyMetrics(BaseSchema):
    """
    'Key Metrics' card in the view dialog.

    fraud_detection_rate   → blue   "12.0%"
    fraud_amount_detected  → green  "63,195.112"
    alert_cases_generated  → orange "91"
    """

    fraud_detection_rate: float = 0.0  # e.g. 12.0  (%)
    fraud_amount_detected: float = 0.0  # e.g. 63195.112  (KES)
    alert_cases_generated: int = 0  # e.g. 91


class ReportDetailResponse(BaseSchema):
    """
    Full report detail — rendered in the View Report dialog.

    Header:              name  +  "Report Details - Generated on <date>"
    4 info cards:        report_type | status | period_label | record_count
    Report Summary card: summary_text
    Key Metrics card:    key_metrics
    Footer buttons:      Close | Download Report (disabled unless completed)
    """

    id: uuid.UUID
    name: str  # "Weekly Fraud Summary"
    # 4 info cards
    report_type: ReportType  # "Summary"
    status: ReportStatus  # "Completed" (green badge)
    period_label: Optional[str] = None  # "2024-02-05 to 2024-02-11"
    record_count: int = 0  # 1,250
    # Report Summary card text
    summary_text: Optional[str] = None
    # Key Metrics card
    key_metrics: ReportKeyMetrics = ReportKeyMetrics()
    # Meta
    generated_at: datetime
    completed_at: Optional[datetime] = None
    generated_by_name: Optional[str] = None
    custom_notes: Optional[str] = None
    # Download
    can_download: bool = False
    download_url: Optional[str] = None
    # Full computed data (for API consumers who need more than the UI shows)
    report_data: Optional[Dict[str, Any]] = None


# ══════════════════════════════════════════════════════════════════════════════
# COMBINED LIST RESPONSE  (page load — returns stats + items together)
# ══════════════════════════════════════════════════════════════════════════════


class ReportListResponse(BaseSchema):
    """
    Single response for the Reports page load.
    Returns stat cards + paginated table rows in one call.
    """

    stats: ReportListStats
    items: List[ReportListItem]
    total: int
    page: int
    page_size: int
    pages: int
