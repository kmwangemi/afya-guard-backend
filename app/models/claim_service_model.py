import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.claim_model import Claim


class ClaimService(Base):
    """
    Individual line items within a claim (service codes, procedures, drugs).
    A claim can have one or many services.
    """

    __tablename__ = "claim_services"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("claims.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    service_code: Mapped[Optional[str]] = mapped_column(
        String(100), index=True, comment="SHA / NHIF service billing code"
    )
    description: Mapped[Optional[str]] = mapped_column(Text)
    quantity: Mapped[Optional[int]] = mapped_column(Integer)
    unit_price: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    total_price: Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    # Flag for upcoding detection
    is_upcoded: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="Set True by UpcodingDetector if service code is inflated",
    )

    # Relationship
    claim: Mapped["Claim"] = relationship("Claim", back_populates="services")

    def __repr__(self) -> str:
        return f"<ClaimService {self.service_code} x{self.quantity}>"
