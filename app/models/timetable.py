"""Timetable models: school-day config, class timetables, and lesson entries."""

import uuid

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TenantScopedModel, TimestampMixin


DEFAULT_DAYS = ["MON", "TUE", "WED", "THU", "FRI"]


def get_default_periods() -> list[dict]:
    """Return a sensible default school day: 8 periods + lunch break."""
    return [
        {"index": 1, "label": "Period 1", "start_time": "08:00", "end_time": "08:40", "is_break": False},
        {"index": 2, "label": "Period 2", "start_time": "08:40", "end_time": "09:20", "is_break": False},
        {"index": 3, "label": "Period 3", "start_time": "09:20", "end_time": "10:00", "is_break": False},
        {"index": 4, "label": "Break", "start_time": "10:00", "end_time": "10:20", "is_break": True},
        {"index": 5, "label": "Period 4", "start_time": "10:20", "end_time": "11:00", "is_break": False},
        {"index": 6, "label": "Period 5", "start_time": "11:00", "end_time": "11:40", "is_break": False},
        {"index": 7, "label": "Lunch", "start_time": "11:40", "end_time": "12:20", "is_break": True},
        {"index": 8, "label": "Period 6", "start_time": "12:20", "end_time": "13:00", "is_break": False},
        {"index": 9, "label": "Period 7", "start_time": "13:00", "end_time": "13:40", "is_break": False},
        {"index": 10, "label": "Period 8", "start_time": "13:40", "end_time": "14:20", "is_break": False},
    ]


class TimetableConfig(TenantScopedModel):
    """Per-tenant school day configuration (days and periods)."""

    __tablename__ = "timetable_configs"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_timetable_config_tenant"),
    )

    days: Mapped[list] = mapped_column(JSONB, nullable=False, default=lambda: list(DEFAULT_DAYS))
    periods: Mapped[list] = mapped_column(JSONB, nullable=False, default=get_default_periods)


class Timetable(TenantScopedModel):
    """A weekly timetable for a class."""

    __tablename__ = "timetables"
    __table_args__ = (
        Index(
            "idx_timetables_active_per_class",
            "class_id",
            unique=True,
            postgresql_where=text("is_active = true AND deleted_at IS NULL"),
        ),
        Index("idx_timetables_tenant", "tenant_id"),
    )

    class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("school_classes.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Relationships
    school_class = relationship("SchoolClass", lazy="selectin")
    entries = relationship(
        "TimetableEntry",
        back_populates="timetable",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class TimetableEntry(Base, TimestampMixin):
    """A single lesson cell: day + period -> subject + teacher."""

    __tablename__ = "timetable_entries"
    __table_args__ = (
        UniqueConstraint(
            "timetable_id", "day", "period_index", name="uq_timetable_entry_slot"
        ),
        Index("idx_timetable_entries_timetable", "timetable_id"),
        Index(
            "idx_timetable_entries_teacher_slot",
            "tenant_id",
            "teacher_id",
            "day",
            "period_index",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    timetable_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("timetables.id", ondelete="CASCADE"),
        nullable=False,
    )
    day: Mapped[str] = mapped_column(String(3), nullable=False)  # MON..FRI
    period_index: Mapped[int] = mapped_column(Integer, nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subjects.id", ondelete="CASCADE"),
        nullable=False,
    )
    teacher_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Relationships
    timetable = relationship("Timetable", back_populates="entries", lazy="selectin")
    subject = relationship("Subject", lazy="selectin")
    teacher = relationship("User", lazy="selectin", foreign_keys=[teacher_id])
