"""Service for managing subjects and grading systems."""

import uuid
from typing import List, Tuple

from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.academic import Subject, ClassSubject, GradingSystem
from app.models.school_class import SchoolClass
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
        """Assign a subject to a class."""
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


# Singleton instance
_academic_service: AcademicService | None = None


def get_academic_service() -> AcademicService:
    """Get the academic service singleton."""
    global _academic_service
    if _academic_service is None:
        _academic_service = AcademicService()
    return _academic_service
