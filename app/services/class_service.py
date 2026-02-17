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
        """Create a new school class."""
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
        await db.refresh(school_class)

        return school_class

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
    ) -> None:
        """Soft delete a class."""
        school_class = await self.get_class(db, class_id)

        school_class.deleted_at = datetime.utcnow()
        school_class.is_active = False

        await db.flush()

    async def get_class_students(
        self,
        db: AsyncSession,
        class_id: uuid.UUID,
        is_active: bool | None = True,
    ) -> list[Student]:
        """Get all students in a class."""
        tenant_id = get_tenant_id()

        # Verify class exists and belongs to tenant
        await self.get_class(db, class_id)

        query = select(Student).where(
            Student.tenant_id == tenant_id,
            Student.class_id == class_id,
            Student.deleted_at.is_(None),
        )

        if is_active is not None:
            query = query.where(Student.is_active == is_active)

        query = query.order_by(Student.first_name, Student.last_name)

        result = await db.execute(query)
        return list(result.scalars().all())

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
        """Get all classes assigned to a teacher."""
        tenant_id = get_tenant_id()

        query = (
            select(SchoolClass)
            .join(TeacherClass, TeacherClass.class_id == SchoolClass.id)
            .where(
                TeacherClass.teacher_id == teacher_id,
                SchoolClass.tenant_id == tenant_id,
                SchoolClass.deleted_at.is_(None),
            )
            .options(
                selectinload(SchoolClass.students),
                selectinload(SchoolClass.grade_level_rel),
            )
        )

        result = await db.execute(query)
        return list(result.scalars().all())

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


def get_class_service() -> ClassService:
    """Get class service instance."""
    return ClassService()
