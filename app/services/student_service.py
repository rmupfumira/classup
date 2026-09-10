"""Student service for CRUD operations."""

import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import logging

from app.exceptions import ConflictException, ForbiddenException, NotFoundException
from app.models import (
    InvitationStatus, ParentInvitation, ParentStudent, SchoolClass,
    Student, TeacherClass, User,
)
from app.models.user import Role
from app.schemas.student import (
    LinkParentRequest,
    ParentEnrollmentInfo,
    StudentCreate,
    StudentUpdate,
)
from app.utils.tenant_context import get_current_user_id, get_current_user_role, get_tenant_id

logger = logging.getLogger(__name__)


class StudentService:
    """Service for managing students."""

    async def get_students(
        self,
        db: AsyncSession,
        class_id: uuid.UUID | None = None,
        grade_level_id: uuid.UUID | None = None,
        age_group: str | None = None,  # DEPRECATED: Use grade_level_id
        is_active: bool | None = True,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Student], int]:
        """Get list of students with optional filters."""
        tenant_id = get_tenant_id()

        query = (
            select(Student)
            .where(Student.tenant_id == tenant_id, Student.deleted_at.is_(None))
            .options(
                selectinload(Student.school_class).selectinload(SchoolClass.grade_level_rel)
            )
        )

        # Teachers only see students from their assigned classes
        role = get_current_user_role()
        if role == Role.TEACHER.value:
            user_id = get_current_user_id()
            subquery = select(TeacherClass.class_id).where(TeacherClass.teacher_id == user_id)
            query = query.where(Student.class_id.in_(subquery))

        # Apply filters
        if class_id:
            query = query.where(Student.class_id == class_id)

        # Filter by grade_level_id (via class relationship)
        if grade_level_id:
            query = query.join(SchoolClass, Student.class_id == SchoolClass.id).where(
                SchoolClass.grade_level_id == grade_level_id
            )

        # DEPRECATED: Keep for backward compatibility
        if age_group:
            query = query.where(Student.age_group == age_group)

        if is_active is not None:
            query = query.where(Student.is_active == is_active)
        if search:
            search_term = f"%{search}%"
            query = query.where(
                (Student.first_name.ilike(search_term))
                | (Student.last_name.ilike(search_term))
            )

        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_query)).scalar() or 0

        # Apply pagination
        query = query.order_by(Student.first_name, Student.last_name)
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(query)
        students = list(result.scalars().unique().all())

        return students, total

    async def get_student(
        self,
        db: AsyncSession,
        student_id: uuid.UUID,
    ) -> Student:
        """Get a single student by ID."""
        tenant_id = get_tenant_id()

        query = (
            select(Student)
            .where(
                Student.id == student_id,
                Student.tenant_id == tenant_id,
                Student.deleted_at.is_(None),
            )
            .options(
                selectinload(Student.school_class).selectinload(SchoolClass.grade_level_rel),
                selectinload(Student.parent_students).selectinload(ParentStudent.parent),
            )
        )

        result = await db.execute(query)
        student = result.scalar_one_or_none()

        if not student:
            raise NotFoundException("Student")

        return student

    async def create_student(
        self,
        db: AsyncSession,
        data: StudentCreate,
    ) -> tuple[Student, list[dict]]:
        """Create a new student and enroll any parents supplied.

        Returns a tuple of (student, parent_results). ``parent_results``
        is a list of ``{email, status, message}`` — the UI uses it to
        surface per-parent outcomes (linked to existing account,
        invitation sent, or per-parent failure). A parent failure never
        rolls back the student — the student is already saved and the
        admin can retry from the detail page.
        """
        tenant_id = get_tenant_id()

        # Validate class belongs to tenant if provided
        if data.class_id:
            await self._validate_class(db, data.class_id)

        student = Student(
            tenant_id=tenant_id,
            first_name=data.first_name,
            last_name=data.last_name,
            date_of_birth=data.date_of_birth,
            gender=data.gender,
            age_group=data.age_group,
            grade_level=data.grade_level,
            class_id=data.class_id,
            medical_info=data.medical_info,
            allergies=data.allergies,
            emergency_contacts=[c.model_dump() for c in data.emergency_contacts],
            notes=data.notes,
            enrollment_date=date.today(),
            is_active=True,
        )

        db.add(student)
        await db.flush()
        await db.refresh(student)

        parent_results: list[dict] = []
        for parent_info in data.parents:
            result = await self._enroll_parent_at_create(db, student, parent_info)
            parent_results.append(result)

        return student, parent_results

    async def _enroll_parent_at_create(
        self,
        db: AsyncSession,
        student: Student,
        info: ParentEnrollmentInfo,
    ) -> dict:
        """Attach one parent to a freshly-created student.

        Two branches:
          * ``linked`` — a PARENT user with that email already exists on
            this tenant. Create the parent_students row, flip
            ``whatsapp_opted_in`` on if the admin gave a phone + ticked
            the box, and fire the "new child added" email + WhatsApp
            mirror. Idempotent — a duplicate link is reported as
            ``already_linked`` rather than raising.
          * ``invited`` — no existing user. Create a ParentInvitation and
            send the invite email; if a phone was captured and the admin
            wants WhatsApp, the parent_signup template goes out too.
            The captured phone is stashed on the invitation row's
            first/last name context only — the actual User row gets
            populated at registration time.

        Errors are swallowed and returned as ``status: "error"`` — the
        student stays saved, the admin can retry the individual parent
        from the detail page.
        """
        from app.config import get_settings
        from app.models import Tenant
        from app.services import parent_notifier
        from app.services.email_service import get_email_service
        from app.services.invitation_service import get_invitation_service

        settings = get_settings()
        tenant_id = student.tenant_id
        email = info.email.lower()

        try:
            existing_parent_stmt = select(User).where(
                User.email == email,
                User.tenant_id == tenant_id,
                User.role == Role.PARENT.value,
                User.deleted_at.is_(None),
            )
            existing_parent = (await db.execute(existing_parent_stmt)).scalar_one_or_none()

            tenant = await db.get(Tenant, tenant_id)
            tenant_name = tenant.name if tenant else "your school"
            email_service = get_email_service()

            if existing_parent:
                dup_stmt = select(ParentStudent).where(
                    ParentStudent.parent_id == existing_parent.id,
                    ParentStudent.student_id == student.id,
                )
                if (await db.execute(dup_stmt)).scalar_one_or_none():
                    return {
                        "email": email,
                        "status": "already_linked",
                        "message": (
                            f"{existing_parent.first_name} {existing_parent.last_name} "
                            f"is already linked to this student."
                        ),
                    }

                if info.is_primary:
                    await self._unset_primary_parent(db, student.id)

                db.add(ParentStudent(
                    parent_id=existing_parent.id,
                    student_id=student.id,
                    relationship_type=info.relationship_type,
                    is_primary=info.is_primary,
                ))
                await db.flush()

                # Sibling case: the parent already has an account —
                # tell them a new child has been added.
                if info.phone and info.send_whatsapp_invite and not existing_parent.whatsapp_phone:
                    existing_parent.whatsapp_phone = info.phone
                if info.send_whatsapp_invite and not existing_parent.whatsapp_opted_in:
                    existing_parent.whatsapp_opted_in = True

                await self._notify_parent_new_child(
                    db, existing_parent, tenant_name,
                    f"{student.first_name} {student.last_name}",
                    email_service, settings,
                )
                return {
                    "email": email,
                    "status": "linked",
                    "message": (
                        f"Linked to existing parent account "
                        f"{existing_parent.first_name} {existing_parent.last_name}. "
                        "They have been notified by email"
                        + (" + WhatsApp" if info.send_whatsapp_invite else "")
                        + "."
                    ),
                }

            # No account yet — create + send invitation.
            invitation_service = get_invitation_service()
            invitation = await invitation_service.create_invitation(
                db,
                student_id=student.id,
                email=email,
                first_name=info.first_name,
                last_name=info.last_name,
            )

            from urllib.parse import urlencode
            register_url = (
                f"{settings.app_base_url}/register?"
                + urlencode({"code": invitation.invitation_code, "email": invitation.email})
            )
            try:
                await email_service.send_parent_invitation(
                    to=invitation.email,
                    tenant_name=tenant_name,
                    student_name=f"{student.first_name} {student.last_name}",
                    invitation_code=invitation.invitation_code,
                    register_url=register_url,
                )
            except Exception:
                logger.exception(
                    "Invitation email failed for %s (student %s)", email, student.id,
                )

            # WhatsApp signup nudge if we have a phone and the admin ticked
            # the box. Best-effort — email is still the authoritative channel.
            whatsapp_sent = False
            if info.phone and info.send_whatsapp_invite:
                try:
                    whatsapp_sent = await parent_notifier.notify_parent_invite(
                        db, None,
                        to_phone=info.phone,
                        tenant_name=tenant_name,
                        code=invitation.invitation_code,
                    )
                except Exception:
                    logger.exception(
                        "WhatsApp signup nudge failed for %s (student %s)",
                        info.phone, student.id,
                    )

            channels = "email"
            if whatsapp_sent:
                channels += " + WhatsApp"
            return {
                "email": email,
                "status": "invited",
                "message": f"Invitation sent by {channels}.",
            }
        except ValueError as e:
            # invitation service raises ValueError for duplicates etc.
            return {"email": email, "status": "error", "message": str(e)}
        except Exception:
            logger.exception(
                "Failed to enroll parent %s for student %s", email, student.id,
            )
            return {
                "email": email,
                "status": "error",
                "message": "Something went wrong — retry from the student page.",
            }

    async def _notify_parent_new_child(
        self,
        db: AsyncSession,
        parent: User,
        tenant_name: str,
        student_name: str,
        email_service,
        settings,
    ) -> None:
        """Email + WhatsApp: 'a new child has been linked to your account'.

        Used both when a sibling is picked at create time and when the
        admin manually links a parent from the student detail page.
        Never raises — every failure is logged and swallowed so the
        parent link still lands.
        """
        from app.services import parent_notifier

        try:
            await email_service.send(
                to=parent.email,
                subject=f"A new child has been linked to your {tenant_name} account",
                template_name="parent_link_child.html",
                context={
                    "parent_name": parent.first_name,
                    "student_name": student_name,
                    "tenant_name": tenant_name,
                    "login_url": f"{settings.app_base_url}/login",
                    "app_name": settings.app_name,
                },
            )
        except Exception:
            logger.exception(
                "child-linked email failed for parent %s", parent.id,
            )
        try:
            await parent_notifier.notify_parent_link_child(
                db, parent,
                tenant_name=tenant_name,
                student_name=student_name,
            )
        except Exception:
            logger.exception(
                "child-linked WhatsApp failed for parent %s", parent.id,
            )

    async def update_student(
        self,
        db: AsyncSession,
        student_id: uuid.UUID,
        data: StudentUpdate,
    ) -> Student:
        """Update a student."""
        student = await self.get_student(db, student_id)

        # Validate class belongs to tenant if provided
        if data.class_id:
            await self._validate_class(db, data.class_id)

        # Update fields
        update_data = data.model_dump(exclude_unset=True)
        if "emergency_contacts" in update_data and update_data["emergency_contacts"]:
            update_data["emergency_contacts"] = [
                c.model_dump() if hasattr(c, "model_dump") else c
                for c in update_data["emergency_contacts"]
            ]

        for field, value in update_data.items():
            setattr(student, field, value)

        await db.flush()
        await db.refresh(student)

        return student

    async def delete_student(
        self,
        db: AsyncSession,
        student_id: uuid.UUID,
    ) -> None:
        """Soft delete a student, plus soft-delete any parents whose ONLY
        child at this tenant was this student.

        Compliance nudge: when a student leaves, POPIA / GDPR expects
        us to stop processing that family's data unless there's another
        legitimate reason to keep it. A parent linked to a sibling who
        still attends the school keeps their account; a parent whose
        only child just left loses tenant access. Historical joins
        (ParentStudent rows) are preserved so old messages / attendance
        records still show the correct parent name — the User row is
        soft-deleted so login is blocked and notifications stop firing.
        """
        from datetime import datetime, timezone

        student = await self.get_student(db, student_id)
        tenant_id = student.tenant_id

        # Snapshot the parent IDs BEFORE the student is soft-deleted —
        # the cleanup query filters by "students still active", so we
        # need the join lookup done first while the row is queryable.
        parent_ids_stmt = select(ParentStudent.parent_id).where(
            ParentStudent.student_id == student_id,
        )
        parent_ids: list[uuid.UUID] = list(
            (await db.execute(parent_ids_stmt)).scalars().all()
        )

        student.deleted_at = datetime.now(timezone.utc)
        student.is_active = False
        await db.flush()

        # Kill any pending parent-invitations for this student. Without
        # this, a link a parent hasn't clicked yet would still work,
        # then land them on a soft-deleted student.
        await self._expire_pending_invites_for_student(db, student_id)

        if parent_ids:
            await self._cleanup_orphaned_parents(db, tenant_id, parent_ids)

    async def _expire_pending_invites_for_student(
        self,
        db: AsyncSession,
        student_id: uuid.UUID,
    ) -> int:
        """Mark all PENDING parent-invitations for a student as EXPIRED.

        Called from delete_student — if we leave PENDING invites in
        place, a parent could accept one after the student's gone and
        end up linked to a soft-deleted student. Marking (rather than
        deleting) preserves the audit trail of who was invited when.
        Returns the count for logging."""
        import logging

        logger = logging.getLogger(__name__)
        stmt = select(ParentInvitation).where(
            ParentInvitation.student_id == student_id,
            ParentInvitation.status == InvitationStatus.PENDING.value,
        )
        rows = list((await db.execute(stmt)).scalars().all())
        for inv in rows:
            inv.status = InvitationStatus.EXPIRED.value
        if rows:
            await db.flush()
            logger.info(
                "Expired %d pending parent-invitation(s) for deleted student %s",
                len(rows), student_id,
            )
        return len(rows)

    async def _cleanup_orphaned_parents(
        self,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        parent_ids: list[uuid.UUID],
    ) -> list[uuid.UUID]:
        """Soft-delete any parent in ``parent_ids`` who no longer has
        an active student at ``tenant_id``.

        Returns the list of user IDs that were actually deleted (for
        callers that want to log / audit / notify — currently just for
        tests). Never raises on individual failures — one bad parent
        row shouldn't stop the others from being cleaned up.
        """
        import logging
        from datetime import datetime, timezone

        logger = logging.getLogger(__name__)
        now = datetime.now(timezone.utc)
        deleted_ids: list[uuid.UUID] = []
        if not parent_ids:
            return deleted_ids

        # For each candidate parent, count how many ACTIVE students they
        # still have at this tenant. If zero, they're orphaned.
        # One query per parent is fine — this fires at most a handful of
        # times per student delete (usually 1-2 parents).
        for parent_id in parent_ids:
            try:
                still_linked_stmt = (
                    select(func.count(Student.id))
                    .join(ParentStudent, ParentStudent.student_id == Student.id)
                    .where(
                        ParentStudent.parent_id == parent_id,
                        Student.tenant_id == tenant_id,
                        Student.deleted_at.is_(None),
                    )
                )
                remaining = (await db.execute(still_linked_stmt)).scalar() or 0
                if remaining > 0:
                    continue  # sibling still enrolled — keep the parent

                parent = await db.get(User, parent_id)
                if parent is None or parent.deleted_at is not None:
                    continue  # already gone
                if parent.role != Role.PARENT.value:
                    # Defensive: never touch staff. A parent_students
                    # row for a non-parent user would be a data bug.
                    logger.warning(
                        "Refusing to orphan-clean non-parent user %s (role=%s)",
                        parent_id, parent.role,
                    )
                    continue

                parent.deleted_at = now
                parent.is_active = False
                deleted_ids.append(parent_id)
                logger.info(
                    "Soft-deleted orphaned parent %s (%s) — no active students remain on tenant %s",
                    parent_id, parent.email, tenant_id,
                )
            except Exception:
                logger.exception(
                    "orphan-parent cleanup failed for parent %s on tenant %s",
                    parent_id, tenant_id,
                )
        if deleted_ids:
            await db.flush()
        return deleted_ids

    async def get_student_parents(
        self,
        db: AsyncSession,
        student_id: uuid.UUID,
    ) -> list[tuple[User, ParentStudent]]:
        """Get parents linked to a student."""
        student = await self.get_student(db, student_id)

        query = (
            select(User, ParentStudent)
            .join(ParentStudent, ParentStudent.parent_id == User.id)
            .where(
                ParentStudent.student_id == student_id,
                User.deleted_at.is_(None),
            )
        )

        result = await db.execute(query)
        return list(result.all())

    async def link_parent(
        self,
        db: AsyncSession,
        student_id: uuid.UUID,
        data: LinkParentRequest,
    ) -> ParentStudent:
        """Link a parent to a student.

        Fires the "new child added to your profile" notification (email
        + WhatsApp mirror) after linking — the sibling flow at student-
        create time and manual admin linking both go through here.
        """
        from app.config import get_settings
        from app.models import Tenant
        from app.services.email_service import get_email_service

        tenant_id = get_tenant_id()
        student = await self.get_student(db, student_id)

        # Validate parent exists and belongs to tenant
        parent_query = select(User).where(
            User.id == data.parent_id,
            User.tenant_id == tenant_id,
            User.role == Role.PARENT,
            User.deleted_at.is_(None),
        )
        result = await db.execute(parent_query)
        parent = result.scalar_one_or_none()

        if not parent:
            raise NotFoundException("Parent")

        # Check if already linked
        existing_query = select(ParentStudent).where(
            ParentStudent.parent_id == data.parent_id,
            ParentStudent.student_id == student_id,
        )
        result = await db.execute(existing_query)
        if result.scalar_one_or_none():
            raise ConflictException("Parent is already linked to this student")

        # If setting as primary, unset other primaries
        if data.is_primary:
            await self._unset_primary_parent(db, student_id)

        link = ParentStudent(
            parent_id=data.parent_id,
            student_id=student_id,
            relationship_type=data.relationship_type,
            is_primary=data.is_primary,
        )

        db.add(link)
        await db.flush()
        await db.refresh(link)

        tenant = await db.get(Tenant, tenant_id)
        await self._notify_parent_new_child(
            db, parent,
            tenant.name if tenant else "your school",
            f"{student.first_name} {student.last_name}",
            get_email_service(),
            get_settings(),
        )

        return link

    async def unlink_parent(
        self,
        db: AsyncSession,
        student_id: uuid.UUID,
        parent_id: uuid.UUID,
    ) -> None:
        """Unlink a parent from a student.

        Runs the same orphan-cleanup as delete_student: if unlinking
        this parent leaves them with no active students on this tenant,
        their account is soft-deleted (POPIA-friendly: we stop
        processing their data once the reason we had it is gone).
        Historical ParentStudent rows for OTHER students are untouched.
        """
        student = await self.get_student(db, student_id)
        tenant_id = student.tenant_id

        query = select(ParentStudent).where(
            ParentStudent.parent_id == parent_id,
            ParentStudent.student_id == student_id,
        )
        result = await db.execute(query)
        link = result.scalar_one_or_none()

        if not link:
            raise NotFoundException("Parent-student link")

        await db.delete(link)
        await db.flush()

        # After the link is gone, check if the parent has any other
        # active students. If not, their account has no reason to exist.
        await self._cleanup_orphaned_parents(db, tenant_id, [parent_id])

    async def get_my_children(
        self,
        db: AsyncSession,
        parent_id: uuid.UUID,
    ) -> list[Student]:
        """Get students linked to a parent (for parent users)."""
        tenant_id = get_tenant_id()
        query = (
            select(Student)
            .join(ParentStudent, ParentStudent.student_id == Student.id)
            .where(
                ParentStudent.parent_id == parent_id,
                Student.tenant_id == tenant_id,
                Student.deleted_at.is_(None),
            )
            .options(
                selectinload(Student.school_class).selectinload(SchoolClass.grade_level_rel)
            )
        )

        result = await db.execute(query)
        return list(result.scalars().all())

    async def _validate_class(
        self,
        db: AsyncSession,
        class_id: uuid.UUID,
    ) -> SchoolClass:
        """Validate that a class exists and belongs to the tenant."""
        tenant_id = get_tenant_id()

        query = select(SchoolClass).where(
            SchoolClass.id == class_id,
            SchoolClass.tenant_id == tenant_id,
            SchoolClass.deleted_at.is_(None),
        )
        result = await db.execute(query)
        school_class = result.scalar_one_or_none()

        if not school_class:
            raise NotFoundException("Class")

        return school_class

    async def _unset_primary_parent(
        self,
        db: AsyncSession,
        student_id: uuid.UUID,
    ) -> None:
        """Unset primary flag on all parent links for a student."""
        query = select(ParentStudent).where(
            ParentStudent.student_id == student_id,
            ParentStudent.is_primary == True,
        )
        result = await db.execute(query)
        for link in result.scalars():
            link.is_primary = False


def get_student_service() -> StudentService:
    """Get student service instance."""
    return StudentService()
