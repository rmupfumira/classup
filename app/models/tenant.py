"""Tenant model for multi-tenancy support."""

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import Boolean, DateTime, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from uuid_extensions import uuid7

from app.models.base import Base, TimestampMixin, SoftDeleteMixin


class EducationType(str, Enum):
    """Type of educational institution."""

    DAYCARE = "DAYCARE"
    PRIMARY_SCHOOL = "PRIMARY_SCHOOL"
    HIGH_SCHOOL = "HIGH_SCHOOL"
    K12 = "K12"
    COMBINED = "COMBINED"


class Tenant(Base, TimestampMixin, SoftDeleteMixin):
    """Multi-tenant organization (school, daycare, etc.)."""

    __tablename__ = "tenants"
    __table_args__ = (
        Index("idx_tenants_slug", "slug", postgresql_where=text("deleted_at IS NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    logo_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    education_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=EducationType.DAYCARE.value,
    )
    settings: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    onboarding_completed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # Relationships
    users = relationship("User", back_populates="tenant", lazy="selectin")
    students = relationship("Student", back_populates="tenant", lazy="selectin")
    school_classes = relationship("SchoolClass", back_populates="tenant", lazy="selectin")

    def get_setting(self, key: str, default: any = None) -> any:
        """Get a setting value by dot-notation key."""
        keys = key.split(".")
        value = self.settings
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default
            if value is None:
                return default
        return value

    def set_setting(self, key: str, value: any) -> None:
        """Set a setting value by dot-notation key."""
        keys = key.split(".")
        settings = self.settings.copy()
        current = settings
        for k in keys[:-1]:
            if k not in current:
                current[k] = {}
            current = current[k]
        current[keys[-1]] = value
        self.settings = settings

    @property
    def terminology(self) -> dict:
        """Get tenant-specific terminology."""
        return self.settings.get("terminology", {})

    @property
    def features(self) -> dict:
        """Get enabled features for this tenant."""
        return self.settings.get("features", {})

    @property
    def branding(self) -> dict:
        """Get branding settings."""
        return self.settings.get("branding", {})

    @property
    def primary_color(self) -> str:
        """Get the primary brand color."""
        return self.branding.get("primary_color", "#7C3AED")

    @property
    def timezone(self) -> str:
        """Get the tenant's timezone."""
        return self.settings.get("timezone", "Africa/Johannesburg")

    @property
    def language(self) -> str:
        """Get the tenant's default language."""
        return self.settings.get("language", "en")


def get_default_tenant_settings(education_type: EducationType) -> dict:
    """Get default settings based on education type."""
    base_features = {
        "attendance_tracking": True,
        "messaging": True,
        "photo_sharing": True,
        "document_sharing": True,
        "parent_communication": True,
    }

    daycare_features = {
        **base_features,
        "daily_reports": True,
        "nap_tracking": True,
        "bathroom_tracking": True,
        "fluid_tracking": True,
        "meal_tracking": True,
        "diaper_tracking": True,
        "homework_tracking": False,
        "grade_tracking": False,
        "behavior_tracking": False,
        "timetable_management": False,
        "subject_management": False,
        "exam_management": False,
        "disciplinary_records": False,
        "whatsapp_enabled": False,
    }

    school_features = {
        **base_features,
        "daily_reports": False,
        "nap_tracking": False,
        "bathroom_tracking": False,
        "fluid_tracking": False,
        "meal_tracking": False,
        "diaper_tracking": False,
        "homework_tracking": True,
        "grade_tracking": True,
        "behavior_tracking": True,
        "timetable_management": True,
        "subject_management": True,
        "exam_management": True,
        "disciplinary_records": True,
        "whatsapp_enabled": False,
    }

    features_map = {
        EducationType.DAYCARE: daycare_features,
        EducationType.PRIMARY_SCHOOL: school_features,
        EducationType.HIGH_SCHOOL: school_features,
        EducationType.K12: {**daycare_features, **school_features, "daily_reports": True},
        EducationType.COMBINED: {**daycare_features, **school_features, "daily_reports": True},
    }

    terminology_map = {
        EducationType.DAYCARE: {
            "student": "child",
            "students": "children",
            "teacher": "educator",
            "teachers": "educators",
            "class": "class",
            "classes": "classes",
            "parent": "parent",
            "parents": "parents",
        },
        EducationType.PRIMARY_SCHOOL: {
            "student": "learner",
            "students": "learners",
            "teacher": "teacher",
            "teachers": "teachers",
            "class": "class",
            "classes": "classes",
            "parent": "parent",
            "parents": "parents",
        },
        EducationType.HIGH_SCHOOL: {
            "student": "learner",
            "students": "learners",
            "teacher": "teacher",
            "teachers": "teachers",
            "class": "class",
            "classes": "classes",
            "parent": "parent",
            "parents": "parents",
        },
    }

    grade_levels_map = {
        EducationType.DAYCARE: ["INFANT", "TODDLER", "PRESCHOOL"],
        EducationType.PRIMARY_SCHOOL: ["GRADE_R", "GRADE_1", "GRADE_2", "GRADE_3",
                                        "GRADE_4", "GRADE_5", "GRADE_6", "GRADE_7"],
        EducationType.HIGH_SCHOOL: ["GRADE_8", "GRADE_9", "GRADE_10", "GRADE_11", "GRADE_12"],
        EducationType.K12: ["INFANT", "TODDLER", "PRESCHOOL", "GRADE_R", "GRADE_1",
                           "GRADE_2", "GRADE_3", "GRADE_4", "GRADE_5", "GRADE_6",
                           "GRADE_7", "GRADE_8", "GRADE_9", "GRADE_10", "GRADE_11", "GRADE_12"],
        EducationType.COMBINED: ["INFANT", "TODDLER", "PRESCHOOL", "GRADE_R", "GRADE_1",
                                 "GRADE_2", "GRADE_3", "GRADE_4", "GRADE_5", "GRADE_6", "GRADE_7"],
    }

    return {
        "education_type": education_type.value,
        "enabled_grade_levels": grade_levels_map.get(education_type, []),
        "features": features_map.get(education_type, daycare_features),
        "terminology": terminology_map.get(education_type, terminology_map[EducationType.DAYCARE]),
        "report_config": {
            "default_report_type": "DAILY_ACTIVITY" if education_type == EducationType.DAYCARE else "PROGRESS_REPORT",
            "enabled_sections": ["meals", "nap", "fluids", "bathroom", "activities", "notes"]
                               if education_type == EducationType.DAYCARE
                               else ["academics", "behavior", "attendance", "notes"],
        },
        "whatsapp": {
            "enabled": False,
            "phone_number_id": None,
            "send_attendance_alerts": True,
            "send_report_notifications": True,
            "send_announcements": True,
        },
        "branding": {
            "primary_color": "#7C3AED",
            "secondary_color": "#5B21B6",
        },
        "timezone": "Africa/Johannesburg",
        "language": "en",
    }
