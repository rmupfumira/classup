"""Photo sharing service for managing class photo galleries."""

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.file_entity import FileEntity
from app.models.photo_share import PhotoShare, PhotoShareFile, PhotoShareTag
from app.models.school_class import SchoolClass, TeacherClass
from app.models.student import ParentStudent, Student
from app.models.user import User
from app.utils.tenant_context import get_current_user_id, get_current_user_role, get_tenant_id

logger = logging.getLogger(__name__)


class PhotoService:
    """Service for managing photo shares."""

    async def create_photo_share(
        self,
        db: AsyncSession,
        data: dict,
    ) -> PhotoShare:
        """Create a new photo share with files and student tags."""
        tenant_id = get_tenant_id()
        user_id = get_current_user_id()
        role = get_current_user_role()

        class_id = data["class_id"]

        # Validate class belongs to tenant
        result = await db.execute(
            select(SchoolClass).where(
                SchoolClass.id == class_id,
                SchoolClass.tenant_id == tenant_id,
                SchoolClass.deleted_at.is_(None),
            )
        )
        school_class = result.scalar_one_or_none()
        if not school_class:
            from app.exceptions import NotFoundException
            raise NotFoundException("Class not found")

        # Teacher must be assigned to the class
        if role == "TEACHER":
            result = await db.execute(
                select(TeacherClass).where(
                    TeacherClass.teacher_id == user_id,
                    TeacherClass.class_id == class_id,
                )
            )
            if not result.scalar_one_or_none():
                from app.exceptions import ForbiddenException
                raise ForbiddenException("You are not assigned to this class")

        # Validate file_ids exist and are PHOTO category
        file_ids = data["file_ids"]
        result = await db.execute(
            select(FileEntity).where(
                FileEntity.id.in_(file_ids),
                FileEntity.tenant_id == tenant_id,
                FileEntity.file_category == "PHOTO",
                FileEntity.deleted_at.is_(None),
            )
        )
        found_files = {f.id for f in result.scalars().all()}
        if len(found_files) != len(file_ids):
            from app.exceptions import NotFoundException
            raise NotFoundException("One or more photo files not found")

        # Validate student_ids belong to the class
        student_ids = data.get("student_ids", [])
        if student_ids:
            result = await db.execute(
                select(Student.id).where(
                    Student.id.in_(student_ids),
                    Student.class_id == class_id,
                    Student.tenant_id == tenant_id,
                    Student.deleted_at.is_(None),
                    Student.is_active == True,
                )
            )
            found_students = {row[0] for row in result.all()}
            if len(found_students) != len(student_ids):
                from app.exceptions import NotFoundException
                raise NotFoundException("One or more students not found in this class")

        # Create the photo share
        photo_share = PhotoShare(
            tenant_id=tenant_id,
            class_id=class_id,
            caption=data.get("caption"),
            shared_by=user_id,
        )
        db.add(photo_share)
        await db.flush()

        # Create file associations
        for i, file_id in enumerate(file_ids):
            psf = PhotoShareFile(
                photo_share_id=photo_share.id,
                file_entity_id=file_id,
                display_order=i,
            )
            db.add(psf)

        # Create student tags
        for sid in student_ids:
            tag = PhotoShareTag(
                photo_share_id=photo_share.id,
                student_id=sid,
            )
            db.add(tag)

        await db.commit()
        await db.refresh(photo_share)

        # Send notifications (don't fail the create)
        try:
            await self._send_notifications(db, photo_share)
        except Exception as e:
            logger.error(f"Failed to send photo share notifications: {e}")

        return photo_share

    async def get_photo_shares(
        self,
        db: AsyncSession,
        class_id: uuid.UUID | None = None,
        page: int = 1,
        page_size: int = 24,
    ) -> tuple[list[PhotoShare], int]:
        """Get paginated photo shares, scoped by role."""
        tenant_id = get_tenant_id()
        role = get_current_user_role()
        user_id = get_current_user_id()

        query = select(PhotoShare).where(
            PhotoShare.tenant_id == tenant_id,
            PhotoShare.deleted_at.is_(None),
        )

        # Role-based scoping
        if role == "TEACHER":
            teacher_class_ids = await self._get_teacher_class_ids(db, user_id)
            if teacher_class_ids:
                query = query.where(PhotoShare.class_id.in_(teacher_class_ids))
            else:
                query = query.where(False)
        elif role == "PARENT":
            # Parents see shares from children's classes + shares where child is tagged
            visible_ids = await self._get_parent_visible_share_ids(db, user_id, tenant_id)
            if visible_ids:
                query = query.where(PhotoShare.id.in_(visible_ids))
            else:
                query = query.where(False)

        # Optional class filter
        if class_id:
            query = query.where(PhotoShare.class_id == class_id)

        # Count
        count_query = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_query)).scalar() or 0

        # Order and paginate
        query = query.order_by(PhotoShare.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(query)
        shares = list(result.scalars().all())

        return shares, total

    async def get_photo_share(
        self,
        db: AsyncSession,
        share_id: uuid.UUID,
    ) -> PhotoShare:
        """Get a single photo share with permission check."""
        tenant_id = get_tenant_id()
        role = get_current_user_role()
        user_id = get_current_user_id()

        result = await db.execute(
            select(PhotoShare).where(
                PhotoShare.id == share_id,
                PhotoShare.tenant_id == tenant_id,
                PhotoShare.deleted_at.is_(None),
            )
        )
        share = result.scalar_one_or_none()

        if not share:
            from app.exceptions import NotFoundException
            raise NotFoundException("Photo share not found")

        # Permission check for parents
        if role == "PARENT":
            visible_ids = await self._get_parent_visible_share_ids(db, user_id, tenant_id)
            if share.id not in visible_ids:
                from app.exceptions import ForbiddenException
                raise ForbiddenException("You don't have access to this photo share")

        # Permission check for teachers
        if role == "TEACHER":
            teacher_class_ids = await self._get_teacher_class_ids(db, user_id)
            if share.class_id not in teacher_class_ids:
                from app.exceptions import ForbiddenException
                raise ForbiddenException("You don't have access to this photo share")

        return share

    async def delete_photo_share(
        self,
        db: AsyncSession,
        share_id: uuid.UUID,
    ) -> bool:
        """Soft delete a photo share. Only creator or admin."""
        share = await self.get_photo_share(db, share_id)

        user_id = get_current_user_id()
        role = get_current_user_role()

        if share.shared_by != user_id and role not in ("SUPER_ADMIN", "SCHOOL_ADMIN"):
            from app.exceptions import ForbiddenException
            raise ForbiddenException("You don't have permission to delete this photo share")

        share.deleted_at = datetime.now(timezone.utc)
        await db.commit()
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

    async def _get_parent_child_ids(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
    ) -> list[uuid.UUID]:
        """Get student IDs of a parent's children."""
        result = await db.execute(
            select(ParentStudent.student_id).where(
                ParentStudent.parent_id == user_id,
            )
        )
        return [row[0] for row in result.all()]

    async def _get_parent_visible_share_ids(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> set[uuid.UUID]:
        """Get photo share IDs visible to a parent (class-based + tag-based)."""
        # Path 1: Shares from children's classes
        parent_class_ids = await self._get_parent_class_ids(db, user_id)
        class_share_ids: set[uuid.UUID] = set()
        if parent_class_ids:
            result = await db.execute(
                select(PhotoShare.id).where(
                    PhotoShare.tenant_id == tenant_id,
                    PhotoShare.deleted_at.is_(None),
                    PhotoShare.class_id.in_(parent_class_ids),
                )
            )
            class_share_ids = {row[0] for row in result.all()}

        # Path 2: Shares where child is tagged
        child_ids = await self._get_parent_child_ids(db, user_id)
        tag_share_ids: set[uuid.UUID] = set()
        if child_ids:
            result = await db.execute(
                select(PhotoShareTag.photo_share_id)
                .join(PhotoShare, PhotoShare.id == PhotoShareTag.photo_share_id)
                .where(
                    PhotoShareTag.student_id.in_(child_ids),
                    PhotoShare.tenant_id == tenant_id,
                    PhotoShare.deleted_at.is_(None),
                )
                .distinct()
            )
            tag_share_ids = {row[0] for row in result.all()}

        return class_share_ids | tag_share_ids

    async def _get_parent_recipient_ids(
        self,
        db: AsyncSession,
        photo_share: PhotoShare,
    ) -> list[uuid.UUID]:
        """Get parent user IDs to notify (parents of class students + tagged students, deduplicated)."""
        tenant_id = get_tenant_id()

        # Parents of students in the class
        class_parent_ids_q = (
            select(ParentStudent.parent_id)
            .join(Student, Student.id == ParentStudent.student_id)
            .where(
                Student.class_id == photo_share.class_id,
                Student.deleted_at.is_(None),
                Student.is_active == True,
            )
        )

        # Parents of tagged students
        tagged_student_ids = [tag.student_id for tag in (photo_share.tags or [])]
        tagged_parent_ids_q = (
            select(ParentStudent.parent_id).where(
                ParentStudent.student_id.in_(tagged_student_ids),
            )
        ) if tagged_student_ids else None

        # Combine: all parents from class + tagged, exclude sharer
        conditions = [User.id.in_(class_parent_ids_q)]
        if tagged_parent_ids_q is not None:
            conditions = [or_(User.id.in_(class_parent_ids_q), User.id.in_(tagged_parent_ids_q))]

        result = await db.execute(
            select(User.id).where(
                User.tenant_id == tenant_id,
                User.is_active == True,
                User.deleted_at.is_(None),
                User.role == "PARENT",
                User.id != photo_share.shared_by,
                *conditions,
            )
        )
        return [row[0] for row in result.all()]

    async def _send_notifications(
        self,
        db: AsyncSession,
        photo_share: PhotoShare,
    ) -> None:
        """Send in-app notifications and emails for a photo share."""
        from app.services.notification_service import get_notification_service

        recipient_ids = await self._get_parent_recipient_ids(db, photo_share)
        if not recipient_ids:
            return

        notification_service = get_notification_service()

        sharer_name = photo_share.sharer_name or "A teacher"
        class_name = photo_share.class_name or "a class"
        photo_count = photo_share.photo_count

        title = f"New Photos: {class_name}"
        body = f"{sharer_name} shared {photo_count} photo{'s' if photo_count != 1 else ''} with {class_name}"
        if photo_share.caption:
            body += f" — {photo_share.caption[:100]}"

        await notification_service.create_bulk_notifications(
            db=db,
            user_ids=recipient_ids,
            title=title,
            body=body,
            notification_type="PHOTO_SHARED",
            reference_type="photo_share",
            reference_id=photo_share.id,
        )

        # Send emails to parent recipients
        try:
            from app.services.email_service import get_email_service

            email_service = get_email_service()

            from app.models.tenant import Tenant
            tenant = await db.get(Tenant, photo_share.tenant_id)
            tenant_name = tenant.name if tenant else "ClassUp"

            result = await db.execute(
                select(User.email).where(
                    User.id.in_(recipient_ids),
                    User.email.isnot(None),
                )
            )
            parent_emails = [row[0] for row in result.all()]

            tagged_names = photo_share.tagged_student_names

            for email in parent_emails:
                try:
                    await email_service.send(
                        to=email,
                        subject=f"New Photos: {class_name}",
                        template_name="photo_shared.html",
                        context={
                            "sharer_name": sharer_name,
                            "class_name": class_name,
                            "photo_count": photo_count,
                            "caption": photo_share.caption,
                            "tagged_students": tagged_names,
                            "tenant_name": tenant_name,
                        },
                        from_name=tenant_name,
                    )
                except Exception as e:
                    logger.error(f"Failed to send photo share email to {email}: {e}")
        except Exception as e:
            logger.error(f"Failed to send photo share emails: {e}")


# Singleton instance
_photo_service: PhotoService | None = None


def get_photo_service() -> PhotoService:
    """Get the photo service singleton."""
    global _photo_service
    if _photo_service is None:
        _photo_service = PhotoService()
    return _photo_service
