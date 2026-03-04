"""Announcement service for managing school/class announcements."""

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.announcement import Announcement, AnnouncementDismissal, AnnouncementSeverity
from app.models.school_class import TeacherClass
from app.models.student import ParentStudent, Student
from app.models.user import User
from app.utils.tenant_context import get_current_user_id, get_current_user_role, get_tenant_id

logger = logging.getLogger(__name__)


class AnnouncementService:
    """Service for managing announcements."""

    async def create_announcement(
        self,
        db: AsyncSession,
        data: dict,
    ) -> Announcement:
        """Create a new announcement."""
        tenant_id = get_tenant_id()
        user_id = get_current_user_id()
        role = get_current_user_role()

        # Permission checks
        if data["level"] == "SCHOOL" and role not in ("SUPER_ADMIN", "SCHOOL_ADMIN"):
            from app.exceptions import ForbiddenException
            raise ForbiddenException("Only school admins can create school-wide announcements")

        if data["level"] == "CLASS" and role == "TEACHER":
            # Verify teacher is assigned to the class
            result = await db.execute(
                select(TeacherClass).where(
                    TeacherClass.teacher_id == user_id,
                    TeacherClass.class_id == data["class_id"],
                )
            )
            if not result.scalar_one_or_none():
                from app.exceptions import ForbiddenException
                raise ForbiddenException("You are not assigned to this class")

        announcement = Announcement(
            tenant_id=tenant_id,
            title=data["title"],
            body=data["body"],
            level=data["level"],
            severity=data.get("severity", "INFO"),
            class_id=data.get("class_id"),
            expires_at=data.get("expires_at"),
            is_pinned=data.get("is_pinned", False),
            created_by=user_id,
        )

        db.add(announcement)
        await db.commit()
        await db.refresh(announcement)

        # Send notifications in background (don't fail the create)
        try:
            await self._send_notifications(db, announcement)
        except Exception as e:
            logger.error(f"Failed to send announcement notifications: {e}")

        return announcement

    async def get_announcement(
        self,
        db: AsyncSession,
        announcement_id: uuid.UUID,
    ) -> Announcement:
        """Get a single announcement by ID."""
        tenant_id = get_tenant_id()

        result = await db.execute(
            select(Announcement).where(
                Announcement.id == announcement_id,
                Announcement.tenant_id == tenant_id,
                Announcement.deleted_at.is_(None),
            )
        )
        announcement = result.scalar_one_or_none()

        if not announcement:
            from app.exceptions import NotFoundException
            raise NotFoundException("Announcement not found")

        return announcement

    async def get_announcements(
        self,
        db: AsyncSession,
        level: str | None = None,
        severity: str | None = None,
        class_id: uuid.UUID | None = None,
        active_only: bool = False,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Announcement], int]:
        """Get paginated announcements with optional filters, scoped by role."""
        tenant_id = get_tenant_id()
        role = get_current_user_role()
        user_id = get_current_user_id()

        query = select(Announcement).where(
            Announcement.tenant_id == tenant_id,
            Announcement.deleted_at.is_(None),
        )

        # Role-based scoping
        if role == "TEACHER":
            # Teachers see SCHOOL-level + their assigned classes
            teacher_class_ids = await self._get_teacher_class_ids(db, user_id)
            query = query.where(
                or_(
                    Announcement.level == "SCHOOL",
                    Announcement.class_id.in_(teacher_class_ids) if teacher_class_ids else False,
                )
            )
        elif role == "PARENT":
            # Parents see SCHOOL-level + their children's classes
            parent_class_ids = await self._get_parent_class_ids(db, user_id)
            query = query.where(
                or_(
                    Announcement.level == "SCHOOL",
                    Announcement.class_id.in_(parent_class_ids) if parent_class_ids else False,
                )
            )

        # Apply filters
        if level:
            query = query.where(Announcement.level == level)
        if severity:
            query = query.where(Announcement.severity == severity)
        if class_id:
            query = query.where(Announcement.class_id == class_id)
        if active_only:
            query = query.where(
                or_(
                    Announcement.expires_at.is_(None),
                    Announcement.expires_at > datetime.now(timezone.utc),
                )
            )

        # Count
        count_query = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_query)).scalar() or 0

        # Order and paginate
        query = query.order_by(
            Announcement.is_pinned.desc(),
            Announcement.created_at.desc(),
        )
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(query)
        announcements = list(result.scalars().all())

        return announcements, total

    async def get_active_announcements(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        class_ids: list[uuid.UUID],
    ) -> list[Announcement]:
        """Get active, non-dismissed announcements for dashboard banners."""
        tenant_id = get_tenant_id()

        # Subquery for dismissed announcement IDs
        dismissed_subq = (
            select(AnnouncementDismissal.announcement_id)
            .where(AnnouncementDismissal.user_id == user_id)
            .subquery()
        )

        query = select(Announcement).where(
            Announcement.tenant_id == tenant_id,
            Announcement.deleted_at.is_(None),
            or_(
                Announcement.expires_at.is_(None),
                Announcement.expires_at > datetime.now(timezone.utc),
            ),
            Announcement.id.notin_(select(dismissed_subq)),
        )

        # Filter by scope: SCHOOL-level OR CLASS-level for user's classes
        if class_ids:
            query = query.where(
                or_(
                    Announcement.level == "SCHOOL",
                    Announcement.class_id.in_(class_ids),
                )
            )
        else:
            query = query.where(Announcement.level == "SCHOOL")

        # Order by severity (EMERGENCY first), then pinned, then date
        # Use CASE for severity ordering
        from sqlalchemy import case
        severity_order = case(
            (Announcement.severity == "EMERGENCY", 0),
            (Announcement.severity == "URGENT", 1),
            (Announcement.severity == "WARNING", 2),
            else_=3,
        )
        query = query.order_by(
            severity_order,
            Announcement.is_pinned.desc(),
            Announcement.created_at.desc(),
        )

        result = await db.execute(query)
        return list(result.scalars().all())

    async def update_announcement(
        self,
        db: AsyncSession,
        announcement_id: uuid.UUID,
        data: dict,
    ) -> Announcement:
        """Update an announcement."""
        announcement = await self.get_announcement(db, announcement_id)

        user_id = get_current_user_id()
        role = get_current_user_role()

        # Only creator or SCHOOL_ADMIN can update
        if announcement.created_by != user_id and role not in ("SUPER_ADMIN", "SCHOOL_ADMIN"):
            from app.exceptions import ForbiddenException
            raise ForbiddenException("You don't have permission to update this announcement")

        for key, value in data.items():
            if value is not None and hasattr(announcement, key):
                setattr(announcement, key, value)

        await db.commit()
        await db.refresh(announcement)
        return announcement

    async def delete_announcement(
        self,
        db: AsyncSession,
        announcement_id: uuid.UUID,
    ) -> bool:
        """Soft-delete an announcement."""
        announcement = await self.get_announcement(db, announcement_id)

        user_id = get_current_user_id()
        role = get_current_user_role()

        if announcement.created_by != user_id and role not in ("SUPER_ADMIN", "SCHOOL_ADMIN"):
            from app.exceptions import ForbiddenException
            raise ForbiddenException("You don't have permission to delete this announcement")

        announcement.deleted_at = datetime.now(timezone.utc)
        await db.commit()
        return True

    async def dismiss_announcement(
        self,
        db: AsyncSession,
        announcement_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> bool:
        """Dismiss an announcement for a user."""
        # Verify announcement exists
        await self.get_announcement(db, announcement_id)

        dismissal = AnnouncementDismissal(
            announcement_id=announcement_id,
            user_id=user_id,
        )

        try:
            db.add(dismissal)
            await db.commit()
        except IntegrityError:
            # Already dismissed — no-op
            await db.rollback()

        return True

    # ============== Private Helpers ==============

    async def _get_teacher_class_ids(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
    ) -> list[uuid.UUID]:
        """Get class IDs assigned to a teacher."""
        result = await db.execute(
            select(TeacherClass.class_id).where(TeacherClass.teacher_id == user_id)
        )
        return [row[0] for row in result.all()]

    async def _get_parent_class_ids(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
    ) -> list[uuid.UUID]:
        """Get class IDs of a parent's children."""
        result = await db.execute(
            select(Student.class_id)
            .join(ParentStudent, ParentStudent.student_id == Student.id)
            .where(
                ParentStudent.parent_id == user_id,
                Student.class_id.isnot(None),
                Student.deleted_at.is_(None),
                Student.is_active == True,
            )
            .distinct()
        )
        return [row[0] for row in result.all()]

    async def _get_recipient_user_ids(
        self,
        db: AsyncSession,
        announcement: Announcement,
    ) -> list[uuid.UUID]:
        """Determine notification recipients based on announcement level."""
        tenant_id = get_tenant_id()

        if announcement.level == "SCHOOL":
            # All active tenant users
            result = await db.execute(
                select(User.id).where(
                    User.tenant_id == tenant_id,
                    User.is_active == True,
                    User.deleted_at.is_(None),
                    User.id != announcement.created_by,  # Don't notify creator
                )
            )
        else:
            # CLASS level: teachers of class + parents of students in class
            teacher_ids_q = select(TeacherClass.teacher_id).where(
                TeacherClass.class_id == announcement.class_id,
            )

            parent_ids_q = (
                select(ParentStudent.parent_id)
                .join(Student, Student.id == ParentStudent.student_id)
                .where(
                    Student.class_id == announcement.class_id,
                    Student.deleted_at.is_(None),
                    Student.is_active == True,
                )
            )

            result = await db.execute(
                select(User.id).where(
                    User.tenant_id == tenant_id,
                    User.is_active == True,
                    User.deleted_at.is_(None),
                    User.id != announcement.created_by,
                    or_(
                        User.id.in_(teacher_ids_q),
                        User.id.in_(parent_ids_q),
                    ),
                )
            )

        return [row[0] for row in result.all()]

    async def _send_notifications(
        self,
        db: AsyncSession,
        announcement: Announcement,
    ) -> None:
        """Send in-app notifications (and emails for URGENT/EMERGENCY)."""
        from app.services.notification_service import get_notification_service

        recipient_ids = await self._get_recipient_user_ids(db, announcement)
        if not recipient_ids:
            return

        notification_service = get_notification_service()

        # In-app notifications
        severity_label = announcement.severity
        title = f"[{severity_label}] {announcement.title}"
        body = announcement.body[:200] + ("..." if len(announcement.body) > 200 else "")

        await notification_service.create_bulk_notifications(
            db=db,
            user_ids=recipient_ids,
            title=title,
            body=body,
            notification_type="ANNOUNCEMENT",
            reference_type="announcement",
            reference_id=announcement.id,
        )

        # Email for URGENT/EMERGENCY
        if announcement.severity in ("URGENT", "EMERGENCY"):
            try:
                from app.services.email_service import get_email_service

                email_service = get_email_service()

                # Get tenant name for from_name
                from app.models.tenant import Tenant
                tenant = await db.get(Tenant, announcement.tenant_id)
                tenant_name = tenant.name if tenant else "ClassUp"

                # Get recipient emails
                result = await db.execute(
                    select(User.email).where(
                        User.id.in_(recipient_ids),
                        User.email.isnot(None),
                    )
                )
                emails = [row[0] for row in result.all()]

                class_name = None
                if announcement.school_class:
                    class_name = announcement.school_class.name

                creator_name = None
                if announcement.creator:
                    creator_name = f"{announcement.creator.first_name} {announcement.creator.last_name}"

                for email in emails:
                    try:
                        await email_service.send(
                            to=email,
                            subject=f"[{severity_label}] {announcement.title}",
                            template_name="announcement_alert.html",
                            context={
                                "severity": announcement.severity,
                                "title": announcement.title,
                                "body": announcement.body,
                                "creator_name": creator_name,
                                "class_name": class_name,
                                "tenant_name": tenant_name,
                            },
                            from_name=tenant_name,
                        )
                    except Exception as e:
                        logger.error(f"Failed to send announcement email to {email}: {e}")
            except Exception as e:
                logger.error(f"Failed to send announcement emails: {e}")


# Singleton instance
_announcement_service: AnnouncementService | None = None


def get_announcement_service() -> AnnouncementService:
    """Get the announcement service singleton."""
    global _announcement_service
    if _announcement_service is None:
        _announcement_service = AnnouncementService()
    return _announcement_service
