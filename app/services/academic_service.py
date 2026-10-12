"""Service for managing subjects and grading systems."""

import uuid
from typing import List, Tuple

from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.academic import (
    Subject,
    ClassSubject,
    ClassSubjectTeacher,
    GradingSystem,
    StudentSubjectOverride,
)
from app.models.school_class import SchoolClass, TeacherClass
from app.models.student import Student
from app.models.user import User
from app.utils.tenant_context import get_tenant_id


class AcademicService:
    """Service for managing academic configuration (subjects, grading)."""

    # ==================== SUBJECTS ====================

    async def get_subjects(
        self,
        db: AsyncSession,
        category: str | None = None,
        is_active: bool | None = True,
        page: int = 1,
        page_size: int = 50,
    ) -> Tuple[List[Subject], int]:
        """Get all subjects for the current tenant."""
        tenant_id = get_tenant_id()

        # Base query
        query = select(Subject).where(
            Subject.tenant_id == tenant_id,
            Subject.deleted_at.is_(None),
        )

        # Apply filters
        if is_active is not None:
            query = query.where(Subject.is_active == is_active)
        if category:
            query = query.where(Subject.category == category)

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_query)).scalar() or 0

        # Apply pagination and ordering
        query = query.order_by(Subject.display_order, Subject.name)
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(query)
        subjects = list(result.scalars().all())

        return subjects, total

    async def get_subject(self, db: AsyncSession, subject_id: uuid.UUID) -> Subject | None:
        """Get a subject by ID."""
        tenant_id = get_tenant_id()

        query = select(Subject).where(
            Subject.id == subject_id,
            Subject.tenant_id == tenant_id,
            Subject.deleted_at.is_(None),
        )
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def create_subject(
        self,
        db: AsyncSession,
        name: str,
        code: str,
        description: str | None = None,
        default_total_marks: int = 100,
        category: str | None = None,
        display_order: int = 0,
    ) -> Subject:
        """Create a new subject."""
        tenant_id = get_tenant_id()

        subject = Subject(
            tenant_id=tenant_id,
            name=name,
            code=code.upper(),
            description=description,
            default_total_marks=default_total_marks,
            category=category,
            display_order=display_order,
            is_active=True,
        )
        db.add(subject)
        await db.commit()
        await db.refresh(subject)
        return subject

    async def update_subject(
        self,
        db: AsyncSession,
        subject_id: uuid.UUID,
        **kwargs,
    ) -> Subject | None:
        """Update a subject."""
        subject = await self.get_subject(db, subject_id)
        if not subject:
            return None

        for key, value in kwargs.items():
            if hasattr(subject, key) and value is not None:
                if key == "code":
                    value = value.upper()
                setattr(subject, key, value)

        await db.commit()
        await db.refresh(subject)
        return subject

    async def delete_subject(self, db: AsyncSession, subject_id: uuid.UUID) -> bool:
        """Soft delete a subject."""
        subject = await self.get_subject(db, subject_id)
        if not subject:
            return False

        from datetime import datetime
        subject.deleted_at = datetime.utcnow()
        await db.commit()
        return True

    # ==================== CLASS SUBJECTS ====================

    async def get_class_subjects(
        self,
        db: AsyncSession,
        class_id: uuid.UUID,
    ) -> List[ClassSubject]:
        """Get all subjects assigned to a class."""
        query = (
            select(ClassSubject)
            .options(selectinload(ClassSubject.subject))
            .where(ClassSubject.class_id == class_id)
            .order_by(ClassSubject.display_order, ClassSubject.subject_id)
        )
        result = await db.execute(query)
        return list(result.scalars().all())

    async def assign_subject_to_class(
        self,
        db: AsyncSession,
        class_id: uuid.UUID,
        subject_id: uuid.UUID,
        total_marks: int | None = None,
        is_compulsory: bool = True,
        display_order: int = 0,
    ) -> ClassSubject:
        """Assign a subject to a class.

        If the class has no ``grading_system_id`` set, this call also
        infers one from the subject's curriculum pack (plus any other
        subjects already assigned to the class) so the class starts
        following the right scale as soon as its first subject lands.
        School admin can override from the class edit page.
        """
        # Check if already assigned
        existing = await db.execute(
            select(ClassSubject).where(
                ClassSubject.class_id == class_id,
                ClassSubject.subject_id == subject_id,
            )
        )
        if existing.scalar_one_or_none():
            raise ValueError("Subject already assigned to this class")

        class_subject = ClassSubject(
            class_id=class_id,
            subject_id=subject_id,
            total_marks=total_marks,
            is_compulsory=is_compulsory,
            display_order=display_order,
        )
        db.add(class_subject)
        await db.flush()

        # Infer the class grading system from its subjects' packs if
        # not already set. Runs on every assign so a mid-term switch
        # (e.g. dropping all ZIMSEC subjects and adding Cambridge) can
        # update the default — unless the admin has pinned a custom
        # system, in which case we don't touch it.
        await self._infer_class_grading_system(db, class_id)

        await db.commit()
        await db.refresh(class_subject)
        return class_subject

    async def update_class_subject(
        self,
        db: AsyncSession,
        class_subject_id: uuid.UUID,
        **kwargs,
    ) -> ClassSubject | None:
        """Update a class-subject assignment."""
        query = select(ClassSubject).where(ClassSubject.id == class_subject_id)
        result = await db.execute(query)
        class_subject = result.scalar_one_or_none()

        if not class_subject:
            return None

        for key, value in kwargs.items():
            if hasattr(class_subject, key):
                setattr(class_subject, key, value)

        await db.commit()
        await db.refresh(class_subject)
        return class_subject

    async def remove_subject_from_class(
        self,
        db: AsyncSession,
        class_id: uuid.UUID,
        subject_id: uuid.UUID,
    ) -> bool:
        """Remove a subject from a class."""
        query = select(ClassSubject).where(
            ClassSubject.class_id == class_id,
            ClassSubject.subject_id == subject_id,
        )
        result = await db.execute(query)
        class_subject = result.scalar_one_or_none()

        if not class_subject:
            return False

        await db.delete(class_subject)
        await db.commit()
        return True

    async def bulk_assign_subjects_to_class(
        self,
        db: AsyncSession,
        class_id: uuid.UUID,
        subject_ids: List[uuid.UUID],
    ) -> List[ClassSubject]:
        """Assign multiple subjects to a class at once."""
        # Get currently assigned subjects
        existing = await self.get_class_subjects(db, class_id)
        existing_subject_ids = {cs.subject_id for cs in existing}

        # Add new subjects
        created = []
        for i, subject_id in enumerate(subject_ids):
            if subject_id not in existing_subject_ids:
                class_subject = ClassSubject(
                    class_id=class_id,
                    subject_id=subject_id,
                    display_order=len(existing) + i,
                )
                db.add(class_subject)
                created.append(class_subject)

        if created:
            await db.flush()
            # Same inferrer as the single-assign path, so a bulk pick
            # on an empty class lands with the right grading scale on
            # the first commit.
            await self._infer_class_grading_system(db, class_id)
            await db.commit()
            for cs in created:
                await db.refresh(cs)

        return created

    # ==================== GRADING SYSTEMS ====================

    async def get_grading_systems(
        self,
        db: AsyncSession,
        is_active: bool | None = True,
        page: int = 1,
        page_size: int = 50,
    ) -> Tuple[List[GradingSystem], int]:
        """Get all grading systems for the current tenant."""
        tenant_id = get_tenant_id()

        query = select(GradingSystem).where(
            GradingSystem.tenant_id == tenant_id,
            GradingSystem.deleted_at.is_(None),
        )

        if is_active is not None:
            query = query.where(GradingSystem.is_active == is_active)

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_query)).scalar() or 0

        # Apply pagination
        query = query.order_by(GradingSystem.is_default.desc(), GradingSystem.name)
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(query)
        systems = list(result.scalars().all())

        return systems, total

    async def get_grading_system(
        self, db: AsyncSession, grading_system_id: uuid.UUID
    ) -> GradingSystem | None:
        """Get a grading system by ID."""
        tenant_id = get_tenant_id()

        query = select(GradingSystem).where(
            GradingSystem.id == grading_system_id,
            GradingSystem.tenant_id == tenant_id,
            GradingSystem.deleted_at.is_(None),
        )
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def get_default_grading_system(self, db: AsyncSession) -> GradingSystem | None:
        """Get the default grading system for the current tenant."""
        tenant_id = get_tenant_id()

        query = select(GradingSystem).where(
            GradingSystem.tenant_id == tenant_id,
            GradingSystem.is_default == True,
            GradingSystem.is_active == True,
            GradingSystem.deleted_at.is_(None),
        )
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def create_grading_system(
        self,
        db: AsyncSession,
        name: str,
        grades: List[dict],
        description: str | None = None,
        is_default: bool = False,
    ) -> GradingSystem:
        """Create a new grading system."""
        tenant_id = get_tenant_id()

        # If this is the default, unset any existing default
        if is_default:
            await self._unset_default_grading_system(db)

        grading_system = GradingSystem(
            tenant_id=tenant_id,
            name=name,
            description=description,
            is_default=is_default,
            is_active=True,
            grades=grades,
        )
        db.add(grading_system)
        await db.commit()
        await db.refresh(grading_system)
        return grading_system

    async def update_grading_system(
        self,
        db: AsyncSession,
        grading_system_id: uuid.UUID,
        **kwargs,
    ) -> GradingSystem | None:
        """Update a grading system."""
        grading_system = await self.get_grading_system(db, grading_system_id)
        if not grading_system:
            return None

        # If setting as default, unset any existing default
        if kwargs.get("is_default"):
            await self._unset_default_grading_system(db)

        for key, value in kwargs.items():
            if hasattr(grading_system, key) and value is not None:
                setattr(grading_system, key, value)

        await db.commit()
        await db.refresh(grading_system)
        return grading_system

    async def delete_grading_system(
        self, db: AsyncSession, grading_system_id: uuid.UUID
    ) -> bool:
        """Soft delete a grading system."""
        grading_system = await self.get_grading_system(db, grading_system_id)
        if not grading_system:
            return False

        from datetime import datetime
        grading_system.deleted_at = datetime.utcnow()
        await db.commit()
        return True

    async def _unset_default_grading_system(self, db: AsyncSession) -> None:
        """Unset the current default grading system."""
        tenant_id = get_tenant_id()

        query = select(GradingSystem).where(
            GradingSystem.tenant_id == tenant_id,
            GradingSystem.is_default == True,
            GradingSystem.deleted_at.is_(None),
        )
        result = await db.execute(query)
        current_default = result.scalar_one_or_none()

        if current_default:
            current_default.is_default = False

    # ==================== SETUP STATUS ====================

    async def get_setup_status(self, db: AsyncSession) -> dict:
        """Get the setup completion status for the current tenant."""
        tenant_id = get_tenant_id()

        # Count classes
        class_count_query = select(func.count()).select_from(
            select(SchoolClass).where(
                SchoolClass.tenant_id == tenant_id,
                SchoolClass.deleted_at.is_(None),
            ).subquery()
        )
        class_count = (await db.execute(class_count_query)).scalar() or 0

        # Count subjects
        subject_count_query = select(func.count()).select_from(
            select(Subject).where(
                Subject.tenant_id == tenant_id,
                Subject.deleted_at.is_(None),
            ).subquery()
        )
        subject_count = (await db.execute(subject_count_query)).scalar() or 0

        # Count grading systems
        grading_count_query = select(func.count()).select_from(
            select(GradingSystem).where(
                GradingSystem.tenant_id == tenant_id,
                GradingSystem.deleted_at.is_(None),
            ).subquery()
        )
        grading_count = (await db.execute(grading_count_query)).scalar() or 0

        # Count teachers
        from app.models.user import User, Role
        teacher_count_query = select(func.count()).select_from(
            select(User).where(
                User.tenant_id == tenant_id,
                User.role == Role.TEACHER.value,
                User.deleted_at.is_(None),
            ).subquery()
        )
        teacher_count = (await db.execute(teacher_count_query)).scalar() or 0

        # Count students
        from app.models.student import Student
        student_count_query = select(func.count()).select_from(
            select(Student).where(
                Student.tenant_id == tenant_id,
                Student.deleted_at.is_(None),
            ).subquery()
        )
        student_count = (await db.execute(student_count_query)).scalar() or 0

        # Define setup items and their completion status
        setup_items = [
            {
                "key": "classes",
                "label": "Classes",
                "description": "Create at least one class",
                "completed": class_count > 0,
                "count": class_count,
                "link": "/classes",
                "action_label": "Add Class",
                "action_link": "/classes/new",
            },
            {
                "key": "subjects",
                "label": "Subjects",
                "description": "Set up subjects for your school",
                "completed": subject_count > 0,
                "count": subject_count,
                "link": "/settings/academic/subjects",
                "action_label": "Add Subject",
                "action_link": "/settings/academic/subjects/create",
            },
            {
                "key": "grading",
                "label": "Grading",
                "description": "Configure your grading scale",
                "completed": grading_count > 0,
                "count": grading_count,
                "link": "/settings/academic/grading",
                "action_label": "Add Grading",
                "action_link": "/settings/academic/grading/create",
            },
            {
                "key": "teachers",
                "label": "Teachers",
                "description": "Add teachers to your school",
                "completed": teacher_count > 0,
                "count": teacher_count,
                "link": "/imports",
                "action_label": "Import",
                "action_link": "/imports/upload",
            },
            {
                "key": "students",
                "label": "Students",
                "description": "Enroll students in your school",
                "completed": student_count > 0,
                "count": student_count,
                "link": "/students",
                "action_label": "Add Student",
                "action_link": "/students/new",
            },
        ]

        # Calculate completion percentage
        completed_count = sum(1 for item in setup_items if item["completed"])
        total_items = len(setup_items)
        percentage = int((completed_count / total_items) * 100) if total_items > 0 else 0

        return {
            "items": setup_items,
            "completed_count": completed_count,
            "total_items": total_items,
            "percentage": percentage,
            "is_complete": completed_count == total_items,
        }

    # ==================== DEFAULT SETUP ====================

    async def setup_default_subjects(self, db: AsyncSession, education_type: str) -> List[Subject]:
        """Create default subjects based on education type."""
        tenant_id = get_tenant_id()

        # Define default subjects by education type
        subjects_by_type = {
            "PRIMARY_SCHOOL": [
                {"name": "English", "code": "ENG", "category": "Language"},
                {"name": "Mathematics", "code": "MATH", "category": "Core"},
                {"name": "Science", "code": "SCI", "category": "Core"},
                {"name": "Social Studies", "code": "SS", "category": "Core"},
                {"name": "Local Language", "code": "LL", "category": "Language"},
                {"name": "Physical Education", "code": "PE", "category": "Elective", "default_total_marks": 50},
                {"name": "Art & Craft", "code": "ART", "category": "Elective", "default_total_marks": 50},
                {"name": "Music", "code": "MUS", "category": "Elective", "default_total_marks": 50},
                {"name": "Life Skills", "code": "LS", "category": "Core", "default_total_marks": 50},
            ],
            "HIGH_SCHOOL": [
                {"name": "English", "code": "ENG", "category": "Language"},
                {"name": "Mathematics", "code": "MATH", "category": "Core"},
                {"name": "Physics", "code": "PHY", "category": "Science"},
                {"name": "Chemistry", "code": "CHEM", "category": "Science"},
                {"name": "Biology", "code": "BIO", "category": "Science"},
                {"name": "History", "code": "HIST", "category": "Humanities"},
                {"name": "Geography", "code": "GEO", "category": "Humanities"},
                {"name": "Economics", "code": "ECON", "category": "Commerce"},
                {"name": "Computer Science", "code": "CS", "category": "Technology"},
                {"name": "Physical Education", "code": "PE", "category": "Elective", "default_total_marks": 50},
            ],
        }

        # Get subject configs for this education type
        subject_configs = subjects_by_type.get(education_type, subjects_by_type["PRIMARY_SCHOOL"])

        created_subjects = []
        for i, config in enumerate(subject_configs):
            subject = Subject(
                tenant_id=tenant_id,
                name=config["name"],
                code=config["code"],
                category=config.get("category"),
                default_total_marks=config.get("default_total_marks", 100),
                display_order=i,
                is_active=True,
            )
            db.add(subject)
            created_subjects.append(subject)

        await db.commit()
        for s in created_subjects:
            await db.refresh(s)

        return created_subjects

    async def setup_default_grading_system(
        self, db: AsyncSession, education_type: str
    ) -> GradingSystem:
        """Create a default grading system based on education type."""
        tenant_id = get_tenant_id()

        # Define grading scales by education type
        grades_by_type = {
            "PRIMARY_SCHOOL": [
                {"min": 80, "max": 100, "grade": "A", "description": "Outstanding", "points": 4.0},
                {"min": 70, "max": 79, "grade": "B", "description": "Very Good", "points": 3.5},
                {"min": 60, "max": 69, "grade": "C", "description": "Good", "points": 3.0},
                {"min": 50, "max": 59, "grade": "D", "description": "Satisfactory", "points": 2.5},
                {"min": 40, "max": 49, "grade": "E", "description": "Needs Improvement", "points": 2.0},
                {"min": 0, "max": 39, "grade": "F", "description": "Fail", "points": 0.0},
            ],
            "HIGH_SCHOOL": [
                {"min": 90, "max": 100, "grade": "A+", "description": "Outstanding", "points": 4.0},
                {"min": 80, "max": 89, "grade": "A", "description": "Excellent", "points": 3.7},
                {"min": 70, "max": 79, "grade": "B+", "description": "Very Good", "points": 3.3},
                {"min": 60, "max": 69, "grade": "B", "description": "Good", "points": 3.0},
                {"min": 50, "max": 59, "grade": "C", "description": "Satisfactory", "points": 2.5},
                {"min": 40, "max": 49, "grade": "D", "description": "Pass", "points": 2.0},
                {"min": 0, "max": 39, "grade": "F", "description": "Fail", "points": 0.0},
            ],
        }

        grades = grades_by_type.get(education_type, grades_by_type["PRIMARY_SCHOOL"])

        grading_system = GradingSystem(
            tenant_id=tenant_id,
            name=f"Standard {education_type.replace('_', ' ').title()} Grading",
            description=f"Default grading scale for {education_type.replace('_', ' ').lower()}",
            is_default=True,
            is_active=True,
            grades=grades,
        )
        db.add(grading_system)
        await db.commit()
        await db.refresh(grading_system)
        return grading_system

    # ==================== CLASS GRADING INFERENCE ====================

    async def _infer_class_grading_system(
        self, db: AsyncSession, class_id: uuid.UUID,
    ) -> None:
        """Set ``school_classes.grading_system_id`` from the most-used
        curriculum among the class's current subjects.

        No-op when:
        - the class already has a grading system set (we never clobber
          a value the admin may have picked), or
        - the class has no subjects with a curriculum tag, or
        - the tenant has no GradingSystem matching the inferred pack's
          name (e.g. the admin deleted it).

        Called from assign_subject_to_class and
        bulk_assign_subjects_to_class after the ClassSubject rows have
        been flushed, so the SELECT below sees the just-added rows.
        """
        from collections import Counter
        from app.models.school_class import SchoolClass
        from app.utils.curriculum_packs import get_pack

        tenant_id = get_tenant_id()

        klass = await db.get(SchoolClass, class_id)
        if klass is None or klass.tenant_id != tenant_id:
            return
        if klass.grading_system_id is not None:
            # Admin has pinned a scale — leave it alone.
            return

        # Collect curriculum_pack_codes of subjects currently on this class.
        rows = (await db.execute(
            select(Subject.curriculum_pack_code)
            .join(ClassSubject, ClassSubject.subject_id == Subject.id)
            .where(
                ClassSubject.class_id == class_id,
                Subject.deleted_at.is_(None),
                Subject.curriculum_pack_code.is_not(None),
            )
        )).all()
        pack_codes = [row[0] for row in rows if row[0]]
        if not pack_codes:
            return

        # Majority wins. Ties resolve to Counter's internal insertion
        # order, which matches the order subjects were assigned — stable
        # enough for our purposes.
        dominant_code = Counter(pack_codes).most_common(1)[0][0]
        pack = get_pack(dominant_code)
        if pack is None or pack.grading_system is None:
            return

        # Find the tenant's grading system that matches this pack's
        # name. Pack-seeded grading systems carry the pack's name
        # verbatim (see apply_curriculum_pack), so this is a reliable
        # lookup. If the admin renamed it, inference quietly stops —
        # same as the "no match" case.
        gs = (await db.execute(
            select(GradingSystem).where(
                GradingSystem.tenant_id == tenant_id,
                GradingSystem.name == pack.grading_system.name,
                GradingSystem.deleted_at.is_(None),
            )
            .limit(1)
        )).scalar_one_or_none()
        if gs is None:
            return

        klass.grading_system_id = gs.id

    async def resolve_class_grading_system(
        self, db: AsyncSession, class_id: uuid.UUID,
    ) -> GradingSystem | None:
        """Return the grading system a class actually uses.

        Resolution order:
        1. ``class.grading_system`` — the per-class scale (admin pick
           or inferred from curriculum).
        2. tenant default (``grading_systems.is_default=True``).
        3. ``None`` — report cards fall back to template-embedded
           bands.
        """
        from app.models.school_class import SchoolClass

        klass = await db.get(SchoolClass, class_id)
        if klass is None:
            return None
        if klass.grading_system_id:
            gs = await db.get(GradingSystem, klass.grading_system_id)
            if gs and gs.deleted_at is None:
                return gs
        return await self.get_default_grading_system(db)

    async def set_class_grading_system(
        self,
        db: AsyncSession,
        class_id: uuid.UUID,
        grading_system_id: uuid.UUID | None,
    ) -> bool:
        """Admin override: pin a grading system on a class, or clear it
        (pass None) to go back to inferred / tenant-default behaviour.
        Returns False if the class or the grading system doesn't exist
        on this tenant."""
        from app.models.school_class import SchoolClass

        tenant_id = get_tenant_id()
        klass = await db.get(SchoolClass, class_id)
        if klass is None or klass.tenant_id != tenant_id:
            return False

        if grading_system_id is not None:
            gs = await db.get(GradingSystem, grading_system_id)
            if gs is None or gs.tenant_id != tenant_id or gs.deleted_at is not None:
                return False

        klass.grading_system_id = grading_system_id
        await db.commit()
        return True

    # ==================== CURRICULUM PACKS ====================

    @staticmethod
    def _resolve_subject_pack_code(sdef, pack) -> str:
        """Which curriculum_pack_code should get stamped onto this subject?

        For single-body packs (ZW_ZIMSEC, ZW_CAMBRIDGE, ZA_CAPS, ...)
        the answer is just the pack code itself. For the Combined
        pack both bodies' subjects coexist, so we need the ORIGINAL
        body code (ZW_ZIMSEC or ZW_CAMBRIDGE) per subject — that's
        what drives the per-subject grading-scale selection on
        report cards.

        SubjectDef codes follow stable prefixes:
          - "ZS-..."  → ZIMSEC subjects
          - "CIE-..." → Cambridge subjects

        Anything else falls back to the pack code (safe for all
        existing single-body packs).
        """
        if pack is not None and pack.code == "ZW_COMBINED":
            if sdef.code.startswith("ZS-"):
                return "ZW_ZIMSEC"
            if sdef.code.startswith("CIE-"):
                return "ZW_CAMBRIDGE"
        return pack.code if pack else ""

    async def apply_curriculum_pack(
        self, db: AsyncSession, pack_code: str,
    ) -> dict:
        """Apply a curriculum pack to the current tenant.

        Idempotent: subjects whose ``code`` already exists (case-
        insensitive, within this tenant) are skipped; only new ones are
        added. Also persists the choice onto
        ``tenant.settings.curriculum_pack`` so the Subjects page can
        show "Currently using: ZIMSEC" and the picker knows what's
        active.

        A pack code of ``CUSTOM`` (or any unknown code) seeds nothing;
        it just records that the tenant opted for custom and returns
        a zero-add summary. That lets the UI treat Custom as a first-
        class choice without a special code path.
        """
        from sqlalchemy.orm.attributes import flag_modified

        from app.models import Tenant
        from app.utils.curriculum_packs import CUSTOM_PACK_CODE, get_pack

        tenant_id = get_tenant_id()
        tenant = await db.get(Tenant, tenant_id)
        if tenant is None:
            raise ValueError(f"Tenant {tenant_id} not found")

        pack = get_pack(pack_code)
        added = 0
        skipped = 0
        subject_names: list[str] = []

        if pack is not None:
            # Pre-fetch existing subject codes once to avoid an insert
            # storm when the pack has many subjects.
            existing_rows = (await db.execute(
                select(Subject.code).where(
                    Subject.tenant_id == tenant_id,
                    Subject.deleted_at.is_(None),
                )
            )).all()
            existing_codes = {row[0].upper() for row in existing_rows if row[0]}

            for i, sdef in enumerate(pack.subjects):
                code_upper = sdef.code.upper()
                if code_upper in existing_codes:
                    skipped += 1
                    continue
                db.add(Subject(
                    tenant_id=tenant_id,
                    name=sdef.name,
                    code=code_upper,
                    description=sdef.description or None,
                    default_total_marks=sdef.default_total_marks,
                    category=sdef.category,
                    display_order=i,
                    is_active=True,
                    # Stamp the subject with its origin pack — powers
                    # the class→grading-system inferrer in
                    # assign_subject_to_class. For the Combined pack
                    # each subject keeps its ORIGINAL body code
                    # (ZIMSEC subjects → ZW_ZIMSEC, Cambridge → ZW_CAMBRIDGE)
                    # so report cards grade each with the right scale.
                    curriculum_pack_code=self._resolve_subject_pack_code(
                        sdef, pack,
                    ),
                ))
                existing_codes.add(code_upper)
                added += 1
                subject_names.append(sdef.name)

        # Seed the pack's grading system(s). We ADD a scale if one
        # with the same NAME isn't already on the tenant — so loading
        # ZIMSEC then Cambridge gives the tenant both scales, each
        # available for per-class inference and admin override. We
        # mark the FIRST added scale as ``is_default=True`` only when
        # the tenant had NO scales before this call, protecting the
        # admin's existing default. ``is_default`` on an additional
        # scale would silently flip the tenant's default, which is
        # surprising.
        #
        # For the Combined pack (``grading_system=None``) we seed
        # BOTH ZIMSEC O-Level AND Cambridge A*-U — the report-card
        # renderer picks the right one per subject via
        # ``subjects.curriculum_pack_code``.
        from app.utils.curriculum_packs import (
            _ZIMSEC_O_LEVEL_GRADING,
            _CAMBRIDGE_GRADING,
        )

        grading_systems_to_seed: list = []
        if pack is not None:
            if pack.grading_system is not None:
                grading_systems_to_seed.append(pack.grading_system)
            elif pack.code == "ZW_COMBINED":
                grading_systems_to_seed.extend(
                    [_ZIMSEC_O_LEVEL_GRADING, _CAMBRIDGE_GRADING],
                )

        grading_system_name: str | None = None
        if grading_systems_to_seed:
            existing_gs_count = (await db.execute(
                select(func.count()).select_from(
                    select(GradingSystem).where(
                        GradingSystem.tenant_id == tenant_id,
                        GradingSystem.deleted_at.is_(None),
                    ).subquery()
                )
            )).scalar() or 0

            added_names: list[str] = []
            for i, gs_def in enumerate(grading_systems_to_seed):
                # Does one with THIS name already exist? Preserves
                # idempotency on re-apply and respects admin-renamed
                # scales.
                same_name_exists = (await db.execute(
                    select(func.count()).select_from(
                        select(GradingSystem).where(
                            GradingSystem.tenant_id == tenant_id,
                            GradingSystem.name == gs_def.name,
                            GradingSystem.deleted_at.is_(None),
                        ).subquery()
                    )
                )).scalar() or 0

                if same_name_exists == 0:
                    is_default = (existing_gs_count == 0 and i == 0)
                    gs = GradingSystem(
                        tenant_id=tenant_id,
                        name=gs_def.name,
                        description=gs_def.description,
                        is_default=is_default,
                        is_active=True,
                        grades=[
                            {
                                "min": b.min, "max": b.max, "grade": b.grade,
                                "description": b.description, "points": b.points,
                            }
                            for b in gs_def.bands
                        ],
                    )
                    db.add(gs)
                    added_names.append(gs_def.name)

            # Report the primary name on single packs for backwards
            # compat; Combined returns both names joined.
            grading_system_name = (
                " + ".join(added_names) if added_names else None
            )

        # Record the choice on the tenant so the UI can show it and the
        # picker can preselect. Store the raw code even for Custom.
        settings = dict(tenant.settings or {})
        settings["curriculum_pack"] = (
            CUSTOM_PACK_CODE if pack is None else pack.code
        )
        tenant.settings = settings
        flag_modified(tenant, "settings")

        await db.commit()

        return {
            "pack_code": settings["curriculum_pack"],
            "pack_name": pack.name if pack else "Custom",
            "added": added,
            "skipped": skipped,
            "sample_names": subject_names[:5],
            "grading_system_added": grading_system_name,
        }


    # ==================== SUBJECT TEACHERS ====================
    #
    # Subject-specialist mode: only when
    # ``tenant.settings.subject_teacher_enabled`` is True. These
    # methods are safe to call even when the mode is off — they
    # don't short-circuit, they just manage the join table. The
    # permission helpers below are where the feature flag actually
    # changes behaviour.

    async def assign_subject_teacher(
        self,
        db: AsyncSession,
        class_id: uuid.UUID,
        subject_id: uuid.UUID,
        teacher_id: uuid.UUID,
    ) -> ClassSubjectTeacher:
        """Assign a teacher to a specific subject within a class.

        Idempotent: if the (class, subject, teacher) triple is already
        linked, returns the existing row rather than raising.
        """
        tenant_id = get_tenant_id()

        # Validate the class belongs to this tenant.
        cls_result = await db.execute(
            select(SchoolClass).where(
                SchoolClass.id == class_id,
                SchoolClass.tenant_id == tenant_id,
                SchoolClass.deleted_at.is_(None),
            )
        )
        if not cls_result.scalar_one_or_none():
            from app.exceptions import NotFoundException
            raise NotFoundException(f"Class {class_id} not found")

        # Validate subject-is-assigned-to-class — can't make someone a
        # subject teacher for a subject the class doesn't even take.
        cs_result = await db.execute(
            select(ClassSubject).where(
                ClassSubject.class_id == class_id,
                ClassSubject.subject_id == subject_id,
            )
        )
        if not cs_result.scalar_one_or_none():
            from app.exceptions import ValidationException
            raise ValidationException(
                "Subject is not assigned to this class — assign the "
                "subject first, then pick a teacher.",
            )

        # Validate teacher belongs to this tenant and is a TEACHER.
        tr_result = await db.execute(
            select(User).where(
                User.id == teacher_id,
                User.tenant_id == tenant_id,
                User.role == "TEACHER",
                User.deleted_at.is_(None),
            )
        )
        if not tr_result.scalar_one_or_none():
            from app.exceptions import NotFoundException
            raise NotFoundException(
                f"Teacher {teacher_id} not found on this tenant",
            )

        # Idempotent — don't duplicate an existing assignment.
        existing = (await db.execute(
            select(ClassSubjectTeacher).where(
                ClassSubjectTeacher.class_id == class_id,
                ClassSubjectTeacher.subject_id == subject_id,
                ClassSubjectTeacher.teacher_id == teacher_id,
            )
        )).scalar_one_or_none()
        if existing:
            return existing

        row = ClassSubjectTeacher(
            class_id=class_id,
            subject_id=subject_id,
            teacher_id=teacher_id,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return row

    async def unassign_subject_teacher(
        self,
        db: AsyncSession,
        class_id: uuid.UUID,
        subject_id: uuid.UUID,
        teacher_id: uuid.UUID,
    ) -> bool:
        """Remove a teacher's assignment to a subject in a class.

        Returns True if a row was deleted, False if nothing matched.
        Validates tenant scope via the class.
        """
        tenant_id = get_tenant_id()
        # Guard: the class must be on this tenant. If not, pretend
        # the row doesn't exist (don't leak which tenant owns it).
        cls = (await db.execute(
            select(SchoolClass).where(
                SchoolClass.id == class_id,
                SchoolClass.tenant_id == tenant_id,
            )
        )).scalar_one_or_none()
        if not cls:
            return False

        row = (await db.execute(
            select(ClassSubjectTeacher).where(
                ClassSubjectTeacher.class_id == class_id,
                ClassSubjectTeacher.subject_id == subject_id,
                ClassSubjectTeacher.teacher_id == teacher_id,
            )
        )).scalar_one_or_none()
        if not row:
            return False
        await db.delete(row)
        await db.flush()
        return True

    async def get_teachers_for_class_subject(
        self,
        db: AsyncSession,
        class_id: uuid.UUID,
        subject_id: uuid.UUID,
    ) -> list[User]:
        """Every teacher currently assigned to this subject in this class."""
        rows = (await db.execute(
            select(ClassSubjectTeacher).where(
                ClassSubjectTeacher.class_id == class_id,
                ClassSubjectTeacher.subject_id == subject_id,
            )
        )).scalars().all()
        return [r.teacher for r in rows]

    async def get_subjects_taught_by_teacher(
        self,
        db: AsyncSession,
        teacher_id: uuid.UUID,
        class_id: uuid.UUID | None = None,
    ) -> list[ClassSubjectTeacher]:
        """Every (class, subject) a teacher is specialised in.

        Scoped to a single class when ``class_id`` is given — handy
        for "what does this teacher own in this class" lookups on
        the class detail page.
        """
        stmt = select(ClassSubjectTeacher).where(
            ClassSubjectTeacher.teacher_id == teacher_id,
        )
        if class_id is not None:
            stmt = stmt.where(ClassSubjectTeacher.class_id == class_id)
        return list((await db.execute(stmt)).scalars().all())

    async def can_teacher_grade_subject(
        self,
        db: AsyncSession,
        *,
        teacher_id: uuid.UUID,
        class_id: uuid.UUID,
        subject_id: uuid.UUID,
        subject_teacher_mode: bool,
    ) -> bool:
        """Centralised permission gate for subject-level writes
        (grading, subject comments, subject-scoped parent messaging).

        Logic:
          - Subject-teacher mode OFF: any teacher assigned to the
            class via ``teacher_classes`` (general class teacher)
            can grade every subject — original behaviour.
          - Subject-teacher mode ON: either
              a) the teacher is the general class teacher for this
                 class (``teacher_classes`` row present), OR
              b) the teacher is explicitly assigned to this subject
                 in this class (``class_subject_teachers`` row).

        School admins (SUPER_ADMIN / SCHOOL_ADMIN) always pass; this
        helper is scoped to the TEACHER role. Callers that already
        branch on role should skip calling this.
        """
        # Shortcut: general class teacher always qualifies.
        general = (await db.execute(
            select(TeacherClass).where(
                TeacherClass.teacher_id == teacher_id,
                TeacherClass.class_id == class_id,
            )
        )).scalar_one_or_none()
        if general is not None:
            return True

        if not subject_teacher_mode:
            # Pre-feature behaviour: without a teacher_classes row the
            # teacher can't grade. The subject-specialist table isn't
            # consulted at all when the feature flag is off.
            return False

        subject_teacher = (await db.execute(
            select(ClassSubjectTeacher).where(
                ClassSubjectTeacher.teacher_id == teacher_id,
                ClassSubjectTeacher.class_id == class_id,
                ClassSubjectTeacher.subject_id == subject_id,
            )
        )).scalar_one_or_none()
        return subject_teacher is not None


    # ==================== STUDENT SUBJECT OVERRIDES ====================
    #
    # Per-student swaps of a class's subject — used in Combined-curriculum
    # schools where the class default is one body (e.g. ZIMSEC Math) but
    # a specific student follows the other (Cambridge Math).

    async def set_student_subject_override(
        self,
        db: AsyncSession,
        student_id: uuid.UUID,
        class_subject_id: uuid.UUID,
        override_subject_id: uuid.UUID,
    ) -> StudentSubjectOverride:
        """Create (or update) an override row.

        Idempotent — if a row for (student, class_subject) already
        exists, its ``override_subject_id`` is updated. Validates that
        all three IDs belong to the current tenant.
        """
        from app.exceptions import NotFoundException, ValidationException

        tenant_id = get_tenant_id()

        # Verify tenant scope — the three FKs cross tables so a bad
        # payload could otherwise create a cross-tenant override.
        student = (await db.execute(
            select(Student).where(
                Student.id == student_id,
                Student.tenant_id == tenant_id,
                Student.deleted_at.is_(None),
            )
        )).scalar_one_or_none()
        if student is None:
            raise NotFoundException(f"Student {student_id} not found")

        cs = (await db.execute(
            select(ClassSubject).where(ClassSubject.id == class_subject_id)
        )).scalar_one_or_none()
        if cs is None or cs.class_id != student.class_id:
            raise ValidationException(
                "Override must target a subject on the student's own class.",
            )

        new_subj = (await db.execute(
            select(Subject).where(
                Subject.id == override_subject_id,
                Subject.tenant_id == tenant_id,
                Subject.deleted_at.is_(None),
            )
        )).scalar_one_or_none()
        if new_subj is None:
            raise NotFoundException(
                f"Override subject {override_subject_id} not found",
            )
        if new_subj.id == cs.subject_id:
            raise ValidationException(
                "Override subject is the same as the class subject — "
                "nothing to override.",
            )

        # Upsert — SA doesn't do ON CONFLICT via the ORM cleanly, so
        # fetch-then-update-or-insert.
        existing = (await db.execute(
            select(StudentSubjectOverride).where(
                StudentSubjectOverride.student_id == student_id,
                StudentSubjectOverride.class_subject_id == class_subject_id,
            )
        )).scalar_one_or_none()
        if existing is not None:
            existing.override_subject_id = override_subject_id
            await db.flush()
            await db.refresh(existing)
            return existing

        row = StudentSubjectOverride(
            student_id=student_id,
            class_subject_id=class_subject_id,
            override_subject_id=override_subject_id,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return row

    async def clear_student_subject_override(
        self,
        db: AsyncSession,
        student_id: uuid.UUID,
        class_subject_id: uuid.UUID,
    ) -> bool:
        """Remove the override — student falls back to the class default."""
        tenant_id = get_tenant_id()
        # Tenant guard via the student row.
        student = await db.get(Student, student_id)
        if student is None or student.tenant_id != tenant_id:
            return False
        row = (await db.execute(
            select(StudentSubjectOverride).where(
                StudentSubjectOverride.student_id == student_id,
                StudentSubjectOverride.class_subject_id == class_subject_id,
            )
        )).scalar_one_or_none()
        if row is None:
            return False
        await db.delete(row)
        await db.flush()
        return True

    async def get_effective_subjects_for_student(
        self,
        db: AsyncSession,
        student_id: uuid.UUID,
    ) -> list[Subject]:
        """Return the list of Subjects this student actually takes.

        Starts from the class's ``class_subjects`` and swaps in any
        per-student overrides. Students without any override rows (the
        common case) get the class default verbatim — pre-feature
        behaviour unchanged.

        Returns Subject rows (not ClassSubject) because consumers
        (reports, grading) care about the subject identity + its
        ``curriculum_pack_code`` for grading-scale selection, not the
        join row.
        """
        student = await db.get(Student, student_id)
        if student is None or student.class_id is None:
            return []

        # Load the class's subjects + the overrides in two queries.
        class_subjects = (await db.execute(
            select(ClassSubject)
            .where(ClassSubject.class_id == student.class_id)
            .order_by(ClassSubject.display_order, ClassSubject.id)
        )).scalars().all()

        overrides = (await db.execute(
            select(StudentSubjectOverride).where(
                StudentSubjectOverride.student_id == student_id,
            )
        )).scalars().all()
        override_map = {o.class_subject_id: o.override_subject_id for o in overrides}

        # Collect subject ids in the right order — override swaps in
        # the alternate subject, otherwise we keep the class default.
        subject_ids: list[uuid.UUID] = []
        for cs in class_subjects:
            subject_ids.append(override_map.get(cs.id, cs.subject_id))

        if not subject_ids:
            return []

        # One SELECT for all subjects (preserve order with an index).
        rows = (await db.execute(
            select(Subject).where(Subject.id.in_(subject_ids))
        )).scalars().all()
        by_id = {s.id: s for s in rows}
        return [by_id[sid] for sid in subject_ids if sid in by_id]

    async def resolve_subject_grading_scales(
        self,
        db: AsyncSession,
        subjects: list[Subject],
    ) -> dict[uuid.UUID, list[dict]]:
        """Map each subject to the grading-scale bands it should be
        graded on, based on its ``curriculum_pack_code``.

        For a single-curriculum tenant every subject grades on the
        class/tenant default and this map stays empty — the renderer
        falls back to the section-level grading. For a Combined-pack
        tenant (ZIMSEC + Cambridge in one class) each subject carries
        a different pack code; this method looks up the matching
        tenant ``GradingSystem`` for each code and returns the grades
        list per subject id. The renderer injects it into the section
        before rendering so a ZIMSEC subject is graded on A-U while a
        Cambridge subject on the SAME report card uses A*-U.

        Lookup chain per subject:
          1. ``subject.curriculum_pack_code`` → pack def →
             pack.grading_system.name
          2. Tenant GradingSystem.name == pack.grading_system.name
          3. GradingSystem.grades
        Any step missing → that subject gets no entry (section-level
        scale wins). Admin-renamed scales break the match quietly,
        same as other inferrers.
        """
        if not subjects:
            return {}

        tenant_id = get_tenant_id()
        from app.utils.curriculum_packs import get_pack

        # Collect unique pack codes with a known default grading system.
        pack_name_by_code: dict[str, str] = {}
        for s in subjects:
            code = s.curriculum_pack_code
            if not code or code in pack_name_by_code:
                continue
            pack = get_pack(code)
            if pack is None or pack.grading_system is None:
                continue
            pack_name_by_code[code] = pack.grading_system.name

        if not pack_name_by_code:
            return {}

        # One query for every candidate grading system by name.
        rows = (await db.execute(
            select(GradingSystem).where(
                GradingSystem.tenant_id == tenant_id,
                GradingSystem.name.in_(list(set(pack_name_by_code.values()))),
                GradingSystem.deleted_at.is_(None),
            )
        )).scalars().all()
        grades_by_name: dict[str, list[dict]] = {gs.name: list(gs.grades or []) for gs in rows}

        out: dict[uuid.UUID, list[dict]] = {}
        for s in subjects:
            code = s.curriculum_pack_code
            if not code:
                continue
            name = pack_name_by_code.get(code)
            if not name:
                continue
            grades = grades_by_name.get(name)
            if grades:
                out[s.id] = grades
        return out


# Singleton instance
_academic_service: AcademicService | None = None


def get_academic_service() -> AcademicService:
    """Get the academic service singleton."""
    global _academic_service
    if _academic_service is None:
        _academic_service = AcademicService()
    return _academic_service
