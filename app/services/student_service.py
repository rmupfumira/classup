"""Student service for CRUD operations."""

import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.exceptions import ConflictException, ForbiddenException, NotFoundException
from app.models import ParentStudent, SchoolClass, Student, User
from app.models.user import Role
from app.schemas.student import (
    LinkParentRequest,
    StudentCreate,
    StudentUpdate,
)
from app.utils.tenant_context import get_current_user_role, get_tenant_id


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
    ) -> Student:
        """Create a new student."""
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

        return student

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
        """Soft delete a student."""
        student = await self.get_student(db, student_id)

        from datetime import datetime

        student.deleted_at = datetime.utcnow()
        student.is_active = False

        await db.flush()

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
        """Link a parent to a student."""
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

        return link

    async def unlink_parent(
        self,
        db: AsyncSession,
        student_id: uuid.UUID,
        parent_id: uuid.UUID,
    ) -> None:
        """Unlink a parent from a student."""
        await self.get_student(db, student_id)

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

    async def get_my_children(
        self,
        db: AsyncSession,
        parent_id: uuid.UUID,
    ) -> list[Student]:
        """Get students linked to a parent (for parent users)."""
        query = (
            select(Student)
            .join(ParentStudent, ParentStudent.student_id == Student.id)
            .where(
                ParentStudent.parent_id == parent_id,
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
