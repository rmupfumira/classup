"""School class service for CRUD operations."""

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.exceptions import ConflictException, ForbiddenException, NotFoundException
from app.models import SchoolClass, Student, TeacherClass, User
from app.models.user import Role
from app.schemas.school_class import (
    AssignTeacherRequest,
    SchoolClassCreate,
    SchoolClassUpdate,
)
from app.utils.tenant_context import get_current_user_id, get_current_user_role, get_tenant_id


class ClassService:
    """Service for managing school classes."""

    async def get_classes(
        self,
        db: AsyncSession,
        is_active: bool | None = True,
        age_group: str | None = None,  # DEPRECATED: Use grade_level_id
        grade_level: str | None = None,  # DEPRECATED: Use grade_level_id
        grade_level_id: uuid.UUID | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[SchoolClass], int]:
        """Get list of classes with optional filters."""
        tenant_id = get_tenant_id()
        role = get_current_user_role()
        user_id = get_current_user_id()

        query = (
            select(SchoolClass)
            .where(SchoolClass.tenant_id == tenant_id, SchoolClass.deleted_at.is_(None))
            .options(
                selectinload(SchoolClass.students),
                selectinload(SchoolClass.teacher_classes).selectinload(TeacherClass.teacher),
                selectinload(SchoolClass.grade_level_rel),
            )
        )

        # Teachers only see their assigned classes
        if role == Role.TEACHER.value:
            query = query.join(TeacherClass).where(TeacherClass.teacher_id == user_id)

        # Apply filters
        if is_active is not None:
            query = query.where(SchoolClass.is_active == is_active)
        if grade_level_id:
            query = query.where(SchoolClass.grade_level_id == grade_level_id)
        # DEPRECATED: Keep for backward compatibility
        if age_group:
            query = query.where(SchoolClass.age_group == age_group)
        if grade_level:
            query = query.where(SchoolClass.grade_level == grade_level)
        if search:
            search_term = f"%{search}%"
            query = query.where(
                (SchoolClass.name.ilike(search_term))
                | (SchoolClass.description.ilike(search_term))
            )

        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_query)).scalar() or 0

        # Apply pagination
        query = query.order_by(SchoolClass.name)
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(query)
        classes = list(result.scalars().unique().all())

        return classes, total

    async def get_class(
        self,
        db: AsyncSession,
        class_id: uuid.UUID,
    ) -> SchoolClass:
        """Get a single class by ID."""
        tenant_id = get_tenant_id()

        query = (
            select(SchoolClass)
            .where(
                SchoolClass.id == class_id,
                SchoolClass.tenant_id == tenant_id,
                SchoolClass.deleted_at.is_(None),
            )
            .options(
                selectinload(SchoolClass.students),
                selectinload(SchoolClass.teacher_classes).selectinload(TeacherClass.teacher),
                selectinload(SchoolClass.grade_level_rel),
            )
        )

        result = await db.execute(query)
        school_class = result.scalar_one_or_none()

        if not school_class:
            raise NotFoundException("Class")

        return school_class

    async def create_class(
        self,
        db: AsyncSession,
        data: SchoolClassCreate,
    ) -> SchoolClass:
        """Create a new school class.

        If the class is attached to a grade level that has subjects
        linked (``grade_level_subjects``), those subjects are
        auto-attached as ``class_subjects`` on this new class. Zero
        clicks for the normal case — admin configures the grade's
        subjects once and every class inherits them.
        """
        tenant_id = get_tenant_id()

        school_class = SchoolClass(
            tenant_id=tenant_id,
            name=data.name,
            description=data.description,
            age_group=data.age_group,  # DEPRECATED
            grade_level=data.grade_level,  # DEPRECATED
            grade_level_id=data.grade_level_id,
            capacity=data.capacity,
            is_active=True,
        )

        db.add(school_class)
        await db.flush()

        # Auto-propagate grade-level subjects. Only runs when a grade
        # level is attached — classes without a grade level stay empty
        # until the admin wires subjects up manually (status quo for
        # pre-grade-level data).
        if data.grade_level_id:
            await self._auto_attach_grade_subjects(
                db, school_class.id, data.grade_level_id,
            )

        await db.refresh(school_class)
        return school_class

    @staticmethod
    async def _auto_attach_grade_subjects(
        db: AsyncSession,
        class_id: uuid.UUID,
        grade_level_id: uuid.UUID,
    ) -> int:
        """Attach every subject linked to this grade level as a
        ``class_subjects`` row on the given class.

        Idempotent — skips any (class, subject) pair that already
        exists. Returns the count of rows actually created so callers
        can report propagation. Grade levels with no subjects linked
        yet produce zero rows and no error.
        """
        from app.models.academic import ClassSubject, GradeLevelSubject

        rows = (await db.execute(
            select(GradeLevelSubject)
            .where(GradeLevelSubject.grade_level_id == grade_level_id)
            .order_by(GradeLevelSubject.display_order)
        )).scalars().all()
        if not rows:
            return 0

        existing = {sid for (sid,) in (await db.execute(
            select(ClassSubject.subject_id)
            .where(ClassSubject.class_id == class_id)
        )).all()}

        created = 0
        for i, link in enumerate(rows):
            if link.subject_id in existing:
                continue
            db.add(ClassSubject(
                class_id=class_id,
                subject_id=link.subject_id,
                is_compulsory=True,
                display_order=i,
            ))
            created += 1
        if created:
            await db.flush()
        return created

    @staticmethod
    def _next_excel_suffix(index: int) -> str:
        """Map 0→A, 1→B, …, 25→Z, 26→AA, 27→AB. Matches the JS helper
        on the create-class form so admins see the same naming scheme
        whether they create one at a time or in a batch."""
        s = ""
        n = index
        while True:
            s = chr(ord("A") + (n % 26)) + s
            if n < 26:
                return s
            n = n // 26 - 1

    async def bulk_create_classes(
        self,
        db: AsyncSession,
        grade_level_id: uuid.UUID,
        count: int,
        capacity: int | None = None,
    ) -> list[SchoolClass]:
        """Create ``count`` classes under one grade level, auto-naming
        them ``{grade_name}A``, ``{grade_name}B``, … picking the next
        free Excel-style suffix so existing classes aren't collided with.

        Returns the created classes in creation order. Raises on an
        invalid grade level or a cross-tenant one.
        """
        from app.exceptions import NotFoundException
        from app.models.grade_level import GradeLevel

        tenant_id = get_tenant_id()
        grade_level = (await db.execute(
            select(GradeLevel).where(
                GradeLevel.id == grade_level_id,
                GradeLevel.tenant_id == tenant_id,
                GradeLevel.deleted_at.is_(None),
            )
        )).scalar_one_or_none()
        if grade_level is None:
            raise NotFoundException("Grade level")

        existing = (await db.execute(
            select(SchoolClass).where(
                SchoolClass.tenant_id == tenant_id,
                SchoolClass.grade_level_id == grade_level_id,
                SchoolClass.deleted_at.is_(None),
            )
        )).scalars().all()

        # Collect used suffixes so we skip over them. Case-insensitive.
        base = grade_level.name.strip()
        import re as _re
        suffix_pattern = _re.compile(
            r"^" + _re.escape(base) + r"\s*([A-Z]+)$",
            flags=_re.IGNORECASE,
        )
        used: set[str] = set()
        for cls in existing:
            m = suffix_pattern.match((cls.name or "").strip())
            if m:
                used.add(m.group(1).upper())

        created: list[SchoolClass] = []
        i = 0
        made = 0
        while made < count and i < 10_000:  # 10k is way beyond any realistic
            suffix = self._next_excel_suffix(i)
            i += 1
            if suffix in used:
                continue
            name = f"{base}{suffix}"
            cls = SchoolClass(
                tenant_id=tenant_id,
                name=name,
                grade_level_id=grade_level_id,
                capacity=capacity,
                is_active=True,
            )
            db.add(cls)
            created.append(cls)
            made += 1
            used.add(suffix)

        await db.flush()

        # Auto-propagate grade-level subjects to each new class — same
        # path as single create_class, so a batch of Form 4A/B/C under
        # a grade that teaches Maths + English lands with those two
        # subjects on each class.
        for cls in created:
            await self._auto_attach_grade_subjects(
                db, cls.id, grade_level_id,
            )

        for cls in created:
            await db.refresh(cls)
        return created

    async def update_class(
        self,
        db: AsyncSession,
        class_id: uuid.UUID,
        data: SchoolClassUpdate,
    ) -> SchoolClass:
        """Update a school class."""
        school_class = await self.get_class(db, class_id)

        # Update fields
        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(school_class, field, value)

        await db.flush()
        await db.refresh(school_class)

        return school_class

    async def delete_class(
        self,
        db: AsyncSession,
        class_id: uuid.UUID,
    ) -> dict:
        """Soft delete a class and unassign any students so their records are
        preserved (class_id set to NULL). Returns a summary of what happened.
        """
        from sqlalchemy import update
        from app.models import Student

        school_class = await self.get_class(db, class_id)
        tenant_id = get_tenant_id()

        # Unassign any active students so student records remain intact
        unassign_result = await db.execute(
            update(Student)
            .where(
                Student.tenant_id == tenant_id,
                Student.class_id == class_id,
                Student.deleted_at.is_(None),
            )
            .values(class_id=None)
        )
        students_unassigned = unassign_result.rowcount or 0

        school_class.deleted_at = datetime.utcnow()
        school_class.is_active = False

        await db.flush()

        return {
            "class_id": str(class_id),
            "students_unassigned": students_unassigned,
        }

    async def get_class_students(
        self,
        db: AsyncSession,
        class_id: uuid.UUID,
        is_active: bool | None = True,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[Student], int]:
        """Get students in a class with pagination."""
        tenant_id = get_tenant_id()

        # Verify class exists and belongs to tenant
        await self.get_class(db, class_id)

        base = select(Student).where(
            Student.tenant_id == tenant_id,
            Student.class_id == class_id,
            Student.deleted_at.is_(None),
        )

        if is_active is not None:
            base = base.where(Student.is_active == is_active)

        count_query = select(func.count()).select_from(base.subquery())
        total = (await db.execute(count_query)).scalar() or 0

        query = base.order_by(Student.first_name, Student.last_name)
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(query)
        return list(result.scalars().all()), total

    async def get_class_teachers(
        self,
        db: AsyncSession,
        class_id: uuid.UUID,
    ) -> list[tuple[User, TeacherClass]]:
        """Get all teachers assigned to a class."""
        await self.get_class(db, class_id)

        query = (
            select(User, TeacherClass)
            .join(TeacherClass, TeacherClass.teacher_id == User.id)
            .where(
                TeacherClass.class_id == class_id,
                User.deleted_at.is_(None),
            )
            .order_by(TeacherClass.is_primary.desc(), User.first_name)
        )

        result = await db.execute(query)
        return list(result.all())

    async def assign_teacher(
        self,
        db: AsyncSession,
        class_id: uuid.UUID,
        data: AssignTeacherRequest,
    ) -> TeacherClass:
        """Assign a teacher to a class."""
        tenant_id = get_tenant_id()

        # Verify class exists
        await self.get_class(db, class_id)

        # Verify teacher exists and is a teacher
        teacher_query = select(User).where(
            User.id == data.teacher_id,
            User.tenant_id == tenant_id,
            User.role == Role.TEACHER,
            User.deleted_at.is_(None),
        )
        result = await db.execute(teacher_query)
        teacher = result.scalar_one_or_none()

        if not teacher:
            raise NotFoundException("Teacher")

        # Check if already assigned
        existing_query = select(TeacherClass).where(
            TeacherClass.teacher_id == data.teacher_id,
            TeacherClass.class_id == class_id,
        )
        result = await db.execute(existing_query)
        if result.scalar_one_or_none():
            raise ConflictException("Teacher is already assigned to this class")

        # If setting as primary, unset other primaries for this class
        if data.is_primary:
            await self._unset_primary_teacher(db, class_id)

        assignment = TeacherClass(
            teacher_id=data.teacher_id,
            class_id=class_id,
            is_primary=data.is_primary,
        )

        db.add(assignment)
        await db.flush()
        await db.refresh(assignment)

        return assignment

    async def remove_teacher(
        self,
        db: AsyncSession,
        class_id: uuid.UUID,
        teacher_id: uuid.UUID,
    ) -> None:
        """Remove a teacher from a class."""
        await self.get_class(db, class_id)

        query = select(TeacherClass).where(
            TeacherClass.teacher_id == teacher_id,
            TeacherClass.class_id == class_id,
        )
        result = await db.execute(query)
        assignment = result.scalar_one_or_none()

        if not assignment:
            raise NotFoundException("Teacher-class assignment")

        await db.delete(assignment)
        await db.flush()

    async def set_primary_teacher(
        self,
        db: AsyncSession,
        class_id: uuid.UUID,
        teacher_id: uuid.UUID,
    ) -> TeacherClass:
        """Set a teacher as the primary teacher for a class."""
        await self.get_class(db, class_id)

        # Find the assignment
        query = select(TeacherClass).where(
            TeacherClass.teacher_id == teacher_id,
            TeacherClass.class_id == class_id,
        )
        result = await db.execute(query)
        assignment = result.scalar_one_or_none()

        if not assignment:
            raise NotFoundException("Teacher-class assignment")

        # Unset other primaries
        await self._unset_primary_teacher(db, class_id)

        # Set this one as primary
        assignment.is_primary = True
        await db.flush()
        await db.refresh(assignment)

        return assignment

    async def get_teacher_classes(
        self,
        db: AsyncSession,
        teacher_id: uuid.UUID,
    ) -> list[SchoolClass]:
        """Get all classes a teacher is associated with.

        Two sources:
          1. ``teacher_classes`` — the general class teacher assignment
             (always consulted).
          2. ``class_subject_teachers`` — subject-specialist
             assignments. Only consulted when the tenant has
             ``subject_teacher_enabled=True``, otherwise the subject
             table is treated as if empty (keeps the old behaviour
             for every pre-feature tenant).

        A teacher who's both a class teacher of one class AND a
        subject teacher in another sees both classes in the picker.
        Deduped by class id.
        """
        tenant_id = get_tenant_id()

        # Resolve whether to union-in the subject-teacher assignments.
        # Lazy import so services don't form a cycle with Tenant.
        from app.models import Tenant
        from app.models.academic import ClassSubjectTeacher

        tenant = await db.get(Tenant, tenant_id)
        include_subject_teaching = bool(
            tenant and tenant.subject_teacher_enabled
        )

        # Base: classes where this teacher is a general class teacher.
        general_q = (
            select(SchoolClass)
            .join(TeacherClass, TeacherClass.class_id == SchoolClass.id)
            .where(
                TeacherClass.teacher_id == teacher_id,
                SchoolClass.tenant_id == tenant_id,
                SchoolClass.deleted_at.is_(None),
            )
            .options(selectinload(SchoolClass.grade_level_rel))
        )
        classes_by_id: dict = {}
        for cls in (await db.execute(general_q)).scalars().all():
            classes_by_id[cls.id] = cls

        # Overlay: classes where this teacher owns at least one
        # subject in the ``class_subject_teachers`` table.
        if include_subject_teaching:
            subject_q = (
                select(SchoolClass)
                .join(
                    ClassSubjectTeacher,
                    ClassSubjectTeacher.class_id == SchoolClass.id,
                )
                .where(
                    ClassSubjectTeacher.teacher_id == teacher_id,
                    SchoolClass.tenant_id == tenant_id,
                    SchoolClass.deleted_at.is_(None),
                )
                .options(selectinload(SchoolClass.grade_level_rel))
                .distinct()
            )
            for cls in (await db.execute(subject_q)).scalars().unique().all():
                classes_by_id.setdefault(cls.id, cls)

        return list(classes_by_id.values())

    async def get_my_classes(
        self,
        db: AsyncSession,
    ) -> list[SchoolClass]:
        """Get classes for the current teacher user."""
        user_id = get_current_user_id()
        return await self.get_teacher_classes(db, user_id)

    async def _unset_primary_teacher(
        self,
        db: AsyncSession,
        class_id: uuid.UUID,
    ) -> None:
        """Unset primary flag on all teacher assignments for a class."""
        query = select(TeacherClass).where(
            TeacherClass.class_id == class_id,
            TeacherClass.is_primary == True,
        )
        result = await db.execute(query)
        for assignment in result.scalars():
            assignment.is_primary = False

    # ========================================================================
    # Per-teacher assignment management (used by the Teachers list page)
    # ------------------------------------------------------------------------
    # The class-detail page already exposes "add teacher to class" — this
    # section inverts the perspective: given a teacher, give me (and let
    # me edit) every (class, subject?) they're attached to. Methods are
    # tenant-scoped and raise NotFoundException on anything that can't
    # be resolved on the current tenant.
    # ========================================================================

    async def _get_tenant_teacher(
        self,
        db: AsyncSession,
        teacher_id: uuid.UUID,
    ) -> User:
        """Load a TEACHER user that belongs to the current tenant.

        Raises NotFoundException if the user doesn't exist, isn't on
        this tenant, isn't a teacher, or has been soft-deleted. Used
        as the guard for every per-teacher assignment mutation.
        """
        tenant_id = get_tenant_id()
        result = await db.execute(
            select(User).where(
                User.id == teacher_id,
                User.tenant_id == tenant_id,
                User.role == Role.TEACHER.value,
                User.deleted_at.is_(None),
            )
        )
        teacher = result.scalar_one_or_none()
        if teacher is None:
            raise NotFoundException("Teacher")
        return teacher

    async def get_teacher_assignments(
        self,
        db: AsyncSession,
        teacher_id: uuid.UUID,
    ) -> dict:
        """Return a teacher's full set of class + subject assignments.

        Shape:
            {
              "general": [TeacherClass],     # eager-loads school_class
              "subject": [ClassSubjectTeacher]  # eager-loads class + subject
            }

        Tenant-scoped — ``NotFoundException`` for a cross-tenant teacher
        id. Even when the tenant has subject-teacher mode off, we still
        return any ``class_subject_teachers`` rows that exist (so the UI
        can show them + the admin can clean them up); the mode gate is
        only enforced on *adding* new subject rows.
        """
        from app.models.academic import ClassSubjectTeacher

        await self._get_tenant_teacher(db, teacher_id)
        tenant_id = get_tenant_id()

        general_q = (
            select(TeacherClass)
            .join(SchoolClass, SchoolClass.id == TeacherClass.class_id)
            .where(
                TeacherClass.teacher_id == teacher_id,
                SchoolClass.tenant_id == tenant_id,
                SchoolClass.deleted_at.is_(None),
            )
            .options(selectinload(TeacherClass.school_class))
        )
        general = list((await db.execute(general_q)).scalars().unique().all())

        subject_q = (
            select(ClassSubjectTeacher)
            .join(SchoolClass, SchoolClass.id == ClassSubjectTeacher.class_id)
            .where(
                ClassSubjectTeacher.teacher_id == teacher_id,
                SchoolClass.tenant_id == tenant_id,
                SchoolClass.deleted_at.is_(None),
            )
            .options(
                selectinload(ClassSubjectTeacher.school_class),
                selectinload(ClassSubjectTeacher.subject),
            )
        )
        subject = list((await db.execute(subject_q)).scalars().unique().all())

        return {"general": general, "subject": subject}

    async def add_general_assignment(
        self,
        db: AsyncSession,
        teacher_id: uuid.UUID,
        class_id: uuid.UUID,
        is_primary: bool = False,
    ) -> TeacherClass:
        """Add (or update) a teacher's general class assignment.

        Idempotent: if the row already exists, we flip ``is_primary``
        to the requested value instead of raising. When
        ``is_primary=True`` any other primary teacher on the same class
        is unset first (there's at most one primary per class).
        """
        await self._get_tenant_teacher(db, teacher_id)
        # Reuses get_class's tenant guard so a cross-tenant class_id raises.
        await self.get_class(db, class_id)

        existing = (await db.execute(
            select(TeacherClass).where(
                TeacherClass.teacher_id == teacher_id,
                TeacherClass.class_id == class_id,
            )
        )).scalar_one_or_none()

        if is_primary:
            await self._unset_primary_teacher(db, class_id)

        if existing is not None:
            existing.is_primary = is_primary
            await db.flush()
            await db.refresh(existing)
            return existing

        assignment = TeacherClass(
            teacher_id=teacher_id,
            class_id=class_id,
            is_primary=is_primary,
        )
        db.add(assignment)
        await db.flush()
        await db.refresh(assignment)
        return assignment

    async def remove_general_assignment(
        self,
        db: AsyncSession,
        teacher_id: uuid.UUID,
        class_id: uuid.UUID,
    ) -> bool:
        """Delete a teacher's general class assignment.

        Returns True if a row was deleted, False if the pair didn't
        exist. Tenant-scoped via ``get_class``.
        """
        await self._get_tenant_teacher(db, teacher_id)
        await self.get_class(db, class_id)

        row = (await db.execute(
            select(TeacherClass).where(
                TeacherClass.teacher_id == teacher_id,
                TeacherClass.class_id == class_id,
            )
        )).scalar_one_or_none()
        if row is None:
            return False
        await db.delete(row)
        await db.flush()
        return True

    async def add_subject_assignment(
        self,
        db: AsyncSession,
        teacher_id: uuid.UUID,
        class_id: uuid.UUID,
        subject_id: uuid.UUID,
    ):
        """Add a teacher as a subject-specialist on a class.

        Blocked when ``tenant.subject_teacher_enabled`` is False —
        raises ValueError in that case (API layer surfaces as a 400).
        Delegates the heavy lifting (subject-on-class check, teacher
        validation, idempotency) to ``AcademicService``.
        """
        from app.models import Tenant
        from app.services.academic_service import get_academic_service

        tenant_id = get_tenant_id()
        tenant = await db.get(Tenant, tenant_id)
        if tenant is None:
            raise NotFoundException("Tenant")
        if not tenant.subject_teacher_enabled:
            raise ValueError(
                "Subject-specialist mode is off for this tenant"
            )

        await self._get_tenant_teacher(db, teacher_id)
        academic = get_academic_service()
        return await academic.assign_subject_teacher(
            db,
            class_id=class_id,
            subject_id=subject_id,
            teacher_id=teacher_id,
        )

    async def remove_subject_assignment(
        self,
        db: AsyncSession,
        teacher_id: uuid.UUID,
        class_id: uuid.UUID,
        subject_id: uuid.UUID,
    ) -> bool:
        """Remove a teacher's subject-specialist assignment.

        Returns True if a row was deleted, False if nothing matched.
        No-op (returns False, doesn't raise) when the tenant has
        subject-teacher mode off — admins may still want to clean up
        legacy rows after turning the mode off.
        """
        from app.services.academic_service import get_academic_service

        await self._get_tenant_teacher(db, teacher_id)
        academic = get_academic_service()
        return await academic.unassign_subject_teacher(
            db,
            class_id=class_id,
            subject_id=subject_id,
            teacher_id=teacher_id,
        )


def get_class_service() -> ClassService:
    """Get class service instance."""
    return ClassService()
