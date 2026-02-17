"""Grade level model for tenant-scoped grade/class level configuration."""

import uuid

from sqlalchemy import Boolean, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import TenantScopedModel


class GradeLevel(TenantScopedModel):
    """A grade level configured for a tenant.

    Examples: "Infant", "Toddler", "Grade 1", "Grade 12", "Year 7"
    Pre-seeded based on education_type, but fully customizable by admins.
    """

    __tablename__ = "grade_levels"
    __table_args__ = (
        Index(
            "idx_grade_levels_tenant",
            "tenant_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_grade_levels_tenant_code",
            "tenant_id",
            "code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_grade_levels_tenant_active",
            "tenant_id",
            postgresql_where=text("deleted_at IS NULL AND is_active = true"),
        ),
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Relationships
    tenant = relationship("Tenant", back_populates="grade_levels", lazy="selectin")
    school_classes = relationship(
        "SchoolClass",
        back_populates="grade_level_rel",
        lazy="selectin",
    )
    report_templates = relationship(
        "ReportTemplate",
        secondary="report_template_grade_levels",
        back_populates="grade_levels",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<GradeLevel {self.code}: {self.name}>"
