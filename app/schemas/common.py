"""Common Pydantic schemas used across the application."""

import uuid
from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class PaginationMeta(BaseModel):
    """Pagination metadata for list responses."""

    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_next: bool
    has_prev: bool


class APIResponse(BaseModel, Generic[T]):
    """Standard API response envelope.

    All JSON API responses use this consistent envelope structure.
    """

    status: str = "success"
    data: T | None = None
    message: str | None = None
    errors: list[dict[str, Any]] | None = None
    pagination: PaginationMeta | None = None


class ErrorDetail(BaseModel):
    """Field-level error detail."""

    field: str
    message: str


class ErrorResponse(BaseModel):
    """Standard error response."""

    status: str = "error"
    message: str
    errors: list[ErrorDetail] | None = None


class PaginationParams(BaseModel):
    """Pagination query parameters."""

    page: int = 1
    page_size: int = 20

    model_config = ConfigDict(extra="forbid")

    @property
    def offset(self) -> int:
        """Calculate offset for database query."""
        return (self.page - 1) * self.page_size


class SortParams(BaseModel):
    """Sorting query parameters."""

    sort_by: str = "created_at"
    sort_order: str = "desc"

    model_config = ConfigDict(extra="forbid")


class BaseSchema(BaseModel):
    """Base schema with common configuration."""

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class TimestampMixin(BaseModel):
    """Mixin for entities with timestamps."""

    created_at: datetime
    updated_at: datetime


class IDMixin(BaseModel):
    """Mixin for entities with UUID IDs."""

    id: uuid.UUID
