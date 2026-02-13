"""Tenant onboarding service."""

import logging
from datetime import date
from uuid import UUID

from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SchoolClass, Tenant, User
from app.utils.tenant_context import get_tenant_id

logger = logging.getLogger(__name__)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class OnboardingService:
    """Service for managing tenant onboarding process."""

    async def get_onboarding_status(
        self,
        db: AsyncSession,
    ) -> dict:
        """Get the current onboarding status for the tenant."""
        tenant_id = get_tenant_id()
        tenant = await db.get(Tenant, tenant_id)

        if not tenant:
            raise ValueError("Tenant not found")

        return {
            "onboarding_completed": tenant.onboarding_completed,
            "tenant_name": tenant.name,
            "education_type": tenant.education_type,
            "has_classes": await self._has_classes(db, tenant_id),
            "has_teachers": await self._has_teachers(db, tenant_id),
        }

    async def _has_classes(self, db: AsyncSession, tenant_id: UUID) -> bool:
        """Check if tenant has any classes."""
        result = await db.execute(
            select(SchoolClass.id)
            .where(
                SchoolClass.tenant_id == tenant_id,
                SchoolClass.deleted_at.is_(None),
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def _has_teachers(self, db: AsyncSession, tenant_id: UUID) -> bool:
        """Check if tenant has any teachers."""
        result = await db.execute(
            select(User.id)
            .where(
                User.tenant_id == tenant_id,
                User.role == "TEACHER",
                User.deleted_at.is_(None),
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def update_school_info(
        self,
        db: AsyncSession,
        name: str | None = None,
        address: str | None = None,
        phone: str | None = None,
        timezone: str | None = None,
    ) -> Tenant:
        """Update school information during onboarding."""
        tenant_id = get_tenant_id()
        tenant = await db.get(Tenant, tenant_id)

        if not tenant:
            raise ValueError("Tenant not found")

        if name:
            tenant.name = name
        if address:
            tenant.address = address
        if phone:
            tenant.phone = phone

        if timezone:
            settings = tenant.settings or {}
            settings["timezone"] = timezone
            tenant.settings = settings

        await db.commit()
        await db.refresh(tenant)

        return tenant

    async def update_education_type(
        self,
        db: AsyncSession,
        education_type: str,
        enabled_features: list[str] | None = None,
    ) -> Tenant:
        """Update education type and features during onboarding."""
        tenant_id = get_tenant_id()
        tenant = await db.get(Tenant, tenant_id)

        if not tenant:
            raise ValueError("Tenant not found")

        tenant.education_type = education_type

        settings = tenant.settings or {}

        # Set default features based on education type
        default_features = self._get_default_features(education_type)
        settings["features"] = default_features

        # Override with selected features if provided
        if enabled_features:
            for feature in enabled_features:
                if feature in settings["features"]:
                    settings["features"][feature] = True

        # Set default terminology based on education type
        settings["terminology"] = self._get_default_terminology(education_type)

        tenant.settings = settings
        await db.commit()
        await db.refresh(tenant)

        return tenant

    def _get_default_features(self, education_type: str) -> dict:
        """Get default features for an education type."""
        base_features = {
            "attendance_tracking": True,
            "messaging": True,
            "photo_sharing": True,
            "document_sharing": True,
            "daily_reports": False,
            "parent_communication": True,
            "nap_tracking": False,
            "bathroom_tracking": False,
            "fluid_tracking": False,
            "meal_tracking": False,
            "diaper_tracking": False,
            "homework_tracking": False,
            "grade_tracking": False,
            "behavior_tracking": False,
            "timetable_management": False,
            "subject_management": False,
            "exam_management": False,
            "disciplinary_records": False,
            "whatsapp_enabled": False,
        }

        if education_type == "DAYCARE":
            base_features.update({
                "daily_reports": True,
                "nap_tracking": True,
                "bathroom_tracking": True,
                "fluid_tracking": True,
                "meal_tracking": True,
                "diaper_tracking": True,
            })
        elif education_type in ("PRIMARY_SCHOOL", "K12"):
            base_features.update({
                "homework_tracking": True,
                "grade_tracking": True,
                "behavior_tracking": True,
            })
        elif education_type == "HIGH_SCHOOL":
            base_features.update({
                "homework_tracking": True,
                "grade_tracking": True,
                "behavior_tracking": True,
                "timetable_management": True,
                "subject_management": True,
                "exam_management": True,
                "disciplinary_records": True,
            })

        return base_features

    def _get_default_terminology(self, education_type: str) -> dict:
        """Get default terminology for an education type."""
        if education_type == "DAYCARE":
            return {
                "student": "child",
                "students": "children",
                "teacher": "educator",
                "teachers": "educators",
                "class": "class",
                "classes": "classes",
                "parent": "parent",
                "parents": "parents",
            }
        else:
            return {
                "student": "student",
                "students": "students",
                "teacher": "teacher",
                "teachers": "teachers",
                "class": "class",
                "classes": "classes",
                "parent": "parent",
                "parents": "parents",
            }

    async def create_classes(
        self,
        db: AsyncSession,
        classes: list[dict],
    ) -> list[SchoolClass]:
        """Create multiple classes during onboarding."""
        tenant_id = get_tenant_id()
        created_classes = []

        for class_data in classes:
            school_class = SchoolClass(
                tenant_id=tenant_id,
                name=class_data["name"],
                description=class_data.get("description"),
                age_group=class_data.get("age_group"),
                grade_level=class_data.get("grade_level"),
                capacity=class_data.get("capacity"),
            )
            db.add(school_class)
            created_classes.append(school_class)

        await db.commit()

        for c in created_classes:
            await db.refresh(c)

        return created_classes

    async def invite_teachers(
        self,
        db: AsyncSession,
        teachers: list[dict],
    ) -> list[dict]:
        """Create teacher accounts and send invitations during onboarding."""
        import secrets

        tenant_id = get_tenant_id()
        results = []

        for teacher_data in teachers:
            email = teacher_data["email"].lower()

            # Check if email already exists
            existing = await db.execute(
                select(User).where(
                    User.tenant_id == tenant_id,
                    User.email == email,
                    User.deleted_at.is_(None),
                )
            )
            if existing.scalar_one_or_none():
                results.append({
                    "email": email,
                    "success": False,
                    "error": "Email already exists",
                })
                continue

            # Create teacher with temporary password
            temp_password = secrets.token_urlsafe(12)

            user = User(
                tenant_id=tenant_id,
                email=email,
                password_hash=pwd_context.hash(temp_password),
                first_name=teacher_data.get("first_name", "Teacher"),
                last_name=teacher_data.get("last_name", ""),
                role="TEACHER",
            )
            db.add(user)
            await db.flush()

            # TODO: Send welcome email with password reset link

            results.append({
                "email": email,
                "success": True,
                "user_id": str(user.id),
            })

        await db.commit()
        return results

    async def complete_onboarding(
        self,
        db: AsyncSession,
    ) -> Tenant:
        """Mark onboarding as complete."""
        tenant_id = get_tenant_id()
        tenant = await db.get(Tenant, tenant_id)

        if not tenant:
            raise ValueError("Tenant not found")

        tenant.onboarding_completed = True
        await db.commit()
        await db.refresh(tenant)

        logger.info(f"Onboarding completed for tenant {tenant_id}")
        return tenant


# Singleton instance
_onboarding_service: OnboardingService | None = None


def get_onboarding_service() -> OnboardingService:
    """Get the onboarding service singleton."""
    global _onboarding_service
    if _onboarding_service is None:
        _onboarding_service = OnboardingService()
    return _onboarding_service
