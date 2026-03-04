"""Document sharing service for managing scoped document shares."""

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_share import DocumentShare, DocumentShareFile, DocumentShareScope, DocumentShareTag
from app.models.file_entity import FileEntity
from app.models.school_class import SchoolClass, TeacherClass
from app.models.student import ParentStudent, Student
from app.models.user import User
from app.utils.tenant_context import get_current_user_id, get_current_user_role, get_tenant_id

logger = logging.getLogger(__name__)


class DocumentService:
    """Service for managing document shares with scope-based visibility."""

    async def create_document_share(
        self,
        db: AsyncSession,
        data: dict,
    ) -> DocumentShare:
        """Create a new document share with files and optional student tags."""
        tenant_id = get_tenant_id()
        user_id = get_current_user_id()
        role = get_current_user_role()

        scope = data["scope"]
        class_id = data.get("class_id")

        # Only SCHOOL_ADMIN can share with SCHOOL scope
        if scope == DocumentShareScope.SCHOOL.value and role not in ("SUPER_ADMIN", "SCHOOL_ADMIN"):
            from app.exceptions import ForbiddenException
            raise ForbiddenException("Only administrators can share school-wide documents")

        # Validate class if scope requires it
        if scope in (DocumentShareScope.CLASS.value, DocumentShareScope.STUDENT.value):
            if not class_id:
                from app.exceptions import ValidationException
                raise ValidationException([{"field": "class_id", "message": "Class is required for this scope"}])

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

        # Validate file_ids exist and are DOCUMENT category
        file_ids = data["file_ids"]
        result = await db.execute(
            select(FileEntity).where(
                FileEntity.id.in_(file_ids),
                FileEntity.tenant_id == tenant_id,
                FileEntity.file_category == "DOCUMENT",
                FileEntity.deleted_at.is_(None),
            )
        )
        found_files = {f.id for f in result.scalars().all()}
        if len(found_files) != len(file_ids):
            from app.exceptions import NotFoundException
            raise NotFoundException("One or more document files not found")

        # Validate student_ids belong to the class (for STUDENT scope)
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

        # Create the document share
        doc_share = DocumentShare(
            tenant_id=tenant_id,
            scope=scope,
            class_id=class_id if scope != DocumentShareScope.SCHOOL.value else None,
            title=data["title"],
            description=data.get("description"),
            shared_by=user_id,
        )
        db.add(doc_share)
        await db.flush()

        # Create file associations
        for i, file_id in enumerate(file_ids):
            dsf = DocumentShareFile(
                document_share_id=doc_share.id,
                file_entity_id=file_id,
                display_order=i,
            )
            db.add(dsf)

        # Create student tags
        for sid in student_ids:
            tag = DocumentShareTag(
                document_share_id=doc_share.id,
                student_id=sid,
            )
            db.add(tag)

        await db.commit()

        # Re-fetch with eager loading
        result = await db.execute(
            select(DocumentShare).where(DocumentShare.id == doc_share.id)
        )
        doc_share = result.scalar_one()

        # Send notifications (don't fail the create)
        try:
            await self._send_notifications(db, doc_share)
        except Exception as e:
            logger.error(f"Failed to send document share notifications: {e}")

        return doc_share

    async def get_document_shares(
        self,
        db: AsyncSession,
        class_id: uuid.UUID | None = None,
        scope: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[DocumentShare], int]:
        """Get paginated document shares, scoped by role."""
        tenant_id = get_tenant_id()
        role = get_current_user_role()
        user_id = get_current_user_id()

        query = select(DocumentShare).where(
            DocumentShare.tenant_id == tenant_id,
            DocumentShare.deleted_at.is_(None),
        )

        # Role-based scoping
        if role == "TEACHER":
            teacher_class_ids = await self._get_teacher_class_ids(db, user_id)
            # Teachers see SCHOOL-scoped + their own class shares
            if teacher_class_ids:
                query = query.where(
                    or_(
                        DocumentShare.scope == DocumentShareScope.SCHOOL.value,
                        DocumentShare.class_id.in_(teacher_class_ids),
                    )
                )
            else:
                query = query.where(DocumentShare.scope == DocumentShareScope.SCHOOL.value)
        elif role == "PARENT":
            visible_ids = await self._get_parent_visible_share_ids(db, user_id, tenant_id)
            if visible_ids:
                query = query.where(DocumentShare.id.in_(visible_ids))
            else:
                query = query.where(False)

        # Optional filters
        if class_id:
            query = query.where(DocumentShare.class_id == class_id)
        if scope:
            query = query.where(DocumentShare.scope == scope)

        # Count
        count_query = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_query)).scalar() or 0

        # Order and paginate
        query = query.order_by(DocumentShare.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(query)
        shares = list(result.scalars().all())

        return shares, total

    async def get_document_share(
        self,
        db: AsyncSession,
        share_id: uuid.UUID,
    ) -> DocumentShare:
        """Get a single document share with permission check."""
        tenant_id = get_tenant_id()
        role = get_current_user_role()
        user_id = get_current_user_id()

        result = await db.execute(
            select(DocumentShare).where(
                DocumentShare.id == share_id,
                DocumentShare.tenant_id == tenant_id,
                DocumentShare.deleted_at.is_(None),
            )
        )
        share = result.scalar_one_or_none()

        if not share:
            from app.exceptions import NotFoundException
            raise NotFoundException("Document share not found")

        # Permission check for parents
        if role == "PARENT":
            visible_ids = await self._get_parent_visible_share_ids(db, user_id, tenant_id)
            if share.id not in visible_ids:
                from app.exceptions import ForbiddenException
                raise ForbiddenException("You don't have access to this document")

        # Permission check for teachers
        if role == "TEACHER":
            if share.scope != DocumentShareScope.SCHOOL.value:
                teacher_class_ids = await self._get_teacher_class_ids(db, user_id)
                if share.class_id not in teacher_class_ids:
                    from app.exceptions import ForbiddenException
                    raise ForbiddenException("You don't have access to this document")

        return share

    async def delete_document_share(
        self,
        db: AsyncSession,
        share_id: uuid.UUID,
    ) -> bool:
        """Soft delete a document share. Only creator or admin."""
        share = await self.get_document_share(db, share_id)

        user_id = get_current_user_id()
        role = get_current_user_role()

        if share.shared_by != user_id and role not in ("SUPER_ADMIN", "SCHOOL_ADMIN"):
            from app.exceptions import ForbiddenException
            raise ForbiddenException("You don't have permission to delete this document share")

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
        """Get document share IDs visible to a parent."""
        visible_ids: set[uuid.UUID] = set()

        # Path 1: All SCHOOL-scoped shares
        result = await db.execute(
            select(DocumentShare.id).where(
                DocumentShare.tenant_id == tenant_id,
                DocumentShare.deleted_at.is_(None),
                DocumentShare.scope == DocumentShareScope.SCHOOL.value,
            )
        )
        visible_ids.update(row[0] for row in result.all())

        # Path 2: CLASS-scoped shares from children's classes
        parent_class_ids = await self._get_parent_class_ids(db, user_id)
        if parent_class_ids:
            result = await db.execute(
                select(DocumentShare.id).where(
                    DocumentShare.tenant_id == tenant_id,
                    DocumentShare.deleted_at.is_(None),
                    DocumentShare.scope == DocumentShareScope.CLASS.value,
                    DocumentShare.class_id.in_(parent_class_ids),
                )
            )
            visible_ids.update(row[0] for row in result.all())

        # Path 3: STUDENT-scoped shares where child is tagged
        child_ids = await self._get_parent_child_ids(db, user_id)
        if child_ids:
            result = await db.execute(
                select(DocumentShareTag.document_share_id)
                .join(DocumentShare, DocumentShare.id == DocumentShareTag.document_share_id)
                .where(
                    DocumentShareTag.student_id.in_(child_ids),
                    DocumentShare.tenant_id == tenant_id,
                    DocumentShare.deleted_at.is_(None),
                )
                .distinct()
            )
            visible_ids.update(row[0] for row in result.all())

        return visible_ids

    async def _get_recipient_ids(
        self,
        db: AsyncSession,
        doc_share: DocumentShare,
    ) -> list[uuid.UUID]:
        """Get parent user IDs to notify based on scope."""
        tenant_id = get_tenant_id()

        if doc_share.scope == DocumentShareScope.SCHOOL.value:
            # All parents in the tenant
            result = await db.execute(
                select(User.id).where(
                    User.tenant_id == tenant_id,
                    User.is_active == True,
                    User.deleted_at.is_(None),
                    User.role == "PARENT",
                    User.id != doc_share.shared_by,
                )
            )
            return [row[0] for row in result.all()]

        elif doc_share.scope == DocumentShareScope.CLASS.value:
            # Parents of students in the class
            result = await db.execute(
                select(User.id)
                .join(ParentStudent, ParentStudent.parent_id == User.id)
                .join(Student, Student.id == ParentStudent.student_id)
                .where(
                    User.tenant_id == tenant_id,
                    User.is_active == True,
                    User.deleted_at.is_(None),
                    User.role == "PARENT",
                    User.id != doc_share.shared_by,
                    Student.class_id == doc_share.class_id,
                    Student.deleted_at.is_(None),
                    Student.is_active == True,
                )
                .distinct()
            )
            return [row[0] for row in result.all()]

        elif doc_share.scope == DocumentShareScope.STUDENT.value:
            # Parents of tagged students
            tagged_student_ids = [tag.student_id for tag in (doc_share.tags or [])]
            if not tagged_student_ids:
                return []
            result = await db.execute(
                select(User.id)
                .join(ParentStudent, ParentStudent.parent_id == User.id)
                .where(
                    User.tenant_id == tenant_id,
                    User.is_active == True,
                    User.deleted_at.is_(None),
                    User.role == "PARENT",
                    User.id != doc_share.shared_by,
                    ParentStudent.student_id.in_(tagged_student_ids),
                )
                .distinct()
            )
            return [row[0] for row in result.all()]

        return []

    async def _send_notifications(
        self,
        db: AsyncSession,
        doc_share: DocumentShare,
    ) -> None:
        """Send in-app notifications and emails for a document share."""
        from app.services.notification_service import get_notification_service

        recipient_ids = await self._get_recipient_ids(db, doc_share)
        if not recipient_ids:
            return

        notification_service = get_notification_service()

        sharer_name = doc_share.sharer_name or "A staff member"
        scope_label = {
            DocumentShareScope.SCHOOL.value: "school-wide",
            DocumentShareScope.CLASS.value: doc_share.class_name or "a class",
            DocumentShareScope.STUDENT.value: ", ".join(doc_share.tagged_student_names) if doc_share.tagged_student_names else "students",
        }.get(doc_share.scope, "")

        title = f"New Document: {doc_share.title}"
        body = f"{sharer_name} shared a document ({scope_label})"
        if doc_share.description:
            body += f" — {doc_share.description[:100]}"

        await notification_service.create_bulk_notifications(
            db=db,
            user_ids=recipient_ids,
            title=title,
            body=body,
            notification_type="DOCUMENT_SHARED",
            reference_type="document_share",
            reference_id=doc_share.id,
        )

        # Send emails
        try:
            from app.services.email_service import get_email_service

            email_service = get_email_service()

            from app.models.tenant import Tenant
            tenant = await db.get(Tenant, doc_share.tenant_id)
            tenant_name = tenant.name if tenant else "ClassUp"

            result = await db.execute(
                select(User.email).where(
                    User.id.in_(recipient_ids),
                    User.email.isnot(None),
                )
            )
            parent_emails = [row[0] for row in result.all()]

            for email in parent_emails:
                try:
                    await email_service.send(
                        to=email,
                        subject=f"New Document: {doc_share.title}",
                        template_name="document_shared.html",
                        context={
                            "sharer_name": sharer_name,
                            "title": doc_share.title,
                            "description": doc_share.description,
                            "scope": doc_share.scope,
                            "scope_label": scope_label,
                            "class_name": doc_share.class_name,
                            "file_count": doc_share.file_count,
                            "tagged_students": doc_share.tagged_student_names,
                            "tenant_name": tenant_name,
                        },
                        from_name=tenant_name,
                    )
                except Exception as e:
                    logger.error(f"Failed to send document share email to {email}: {e}")
        except Exception as e:
            logger.error(f"Failed to send document share emails: {e}")


# Singleton instance
_document_service: DocumentService | None = None


def get_document_service() -> DocumentService:
    """Get the document service singleton."""
    global _document_service
    if _document_service is None:
        _document_service = DocumentService()
    return _document_service
