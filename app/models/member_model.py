import uuid
from datetime import UTC, datetime, date
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import DateTime, Enum, String, Date
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums_model import Gender

if TYPE_CHECKING:
    from app.models.claim_model import Claim


class Member(Base):
    """
    SHA health insurance member (beneficiary).
    Snapshot from SHA — PII fields should be encrypted at rest.
    """

    __tablename__ = "members"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # SHA identifiers
    sha_member_id: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
        index=True,
        comment="SHA-issued member ID",
    )
    national_id: Mapped[Optional[str]] = mapped_column(
        String(20), index=True, comment="Kenya National ID — treat as PII"
    )
    # Demographics
    gender: Mapped[Optional[Gender]] = mapped_column(Enum(Gender, name="gender_enum"))
    date_of_birth: Mapped[Optional[date]] = mapped_column(
        Date, comment="PII — encrypt at rest"
    )
    county: Mapped[Optional[str]] = mapped_column(String(100))
    # Coverage
    coverage_status: Mapped[Optional[str]] = mapped_column(
        String(50), comment="ACTIVE / INACTIVE / SUSPENDED"
    )
    scheme: Mapped[Optional[str]] = mapped_column(
        String(100), comment="e.g. Social Health Insurance Fund"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    claims: Mapped[List["Claim"]] = relationship("Claim", back_populates="member")

    def __repr__(self) -> str:
        return f"<Member {self.sha_member_id}>"
