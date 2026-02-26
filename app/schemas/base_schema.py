"""
SHA Fraud Detection — Shared Base Schemas

Common Pydantic building blocks reused across all domain schemas.
"""

import uuid
from datetime import datetime
from typing import Generic, List, Optional, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class BaseSchema(BaseModel):
    """Base for all schemas — enables ORM mode globally."""

    model_config = ConfigDict(from_attributes=True)


class TimestampMixin(BaseSchema):
    """Add created_at / updated_at to any schema."""

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class UUIDSchema(BaseSchema):
    """Add id field to any schema."""

    id: uuid.UUID


class PaginatedResponse(BaseSchema, Generic[T]):
    """
    Standard paginated API response wrapper.

    Example:
        PaginatedResponse[ClaimResponse]
    """

    items: List[T]
    total: int
    page: int
    page_size: int
    pages: int


class MessageResponse(BaseSchema):
    """Simple success/info message response."""

    message: str
    success: bool = True


class ErrorResponse(BaseSchema):
    """Standard error response shape."""

    detail: str
    code: Optional[str] = None
