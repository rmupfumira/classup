"""User service for CRUD operations."""

import secrets
import uuid

from passlib.context import CryptContext
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ConflictException, NotFoundException
from app.models import User
from app.models.user import Role
from app.utils.security import hash_password
from app.utils.tenant_context import get_tenant_id

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class UserService:
    """Service for managing users."""

    async def get_users_by_role(
        self,
        db: AsyncSession,
        role: Role | str,
    ) -> list[User]:
        """Get all users with a specific role in the current tenant."""
        tenant_id = get_tenant_id()

        role_value = role.value if isinstance(role, Role) else role

        query = select(User).where(
            User.tenant_id == tenant_id,
            User.role == role_value,
            User.deleted_at.is_(None),
            User.is_active == True,
        ).order_by(User.first_name, User.last_name)

        result = await db.execute(query)
        return list(result.scalars().all())

    async def get_user(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
    ) -> User:
        """Get a single user by ID."""
        tenant_id = get_tenant_id()

        query = select(User).where(
            User.id == user_id,
            User.tenant_id == tenant_id,
            User.deleted_at.is_(None),
        )

        result = await db.execute(query)
        user = result.scalar_one_or_none()

        if not user:
            raise NotFoundException("User")

        return user

    async def get_teachers(
        self,
        db: AsyncSession,
    ) -> list[User]:
        """Get all teachers in the current tenant."""
        return await self.get_users_by_role(db, Role.TEACHER)

    async def get_parents(
        self,
        db: AsyncSession,
    ) -> list[User]:
        """Get all parents in the current tenant."""
        return await self.get_users_by_role(db, Role.PARENT)

    async def get_teachers_paginated(
        self,
        db: AsyncSession,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[User], int]:
        """Get teachers with pagination and optional search."""
        tenant_id = get_tenant_id()

        base_filter = [
            User.tenant_id == tenant_id,
            User.role == Role.TEACHER.value,
            User.deleted_at.is_(None),
        ]

        if search:
            search_term = f"%{search}%"
            base_filter.append(
                (User.first_name.ilike(search_term))
                | (User.last_name.ilike(search_term))
                | (User.email.ilike(search_term))
            )

        # Count
        count_query = select(func.count(User.id)).where(*base_filter)
        total = (await db.execute(count_query)).scalar() or 0

        # Fetch
        query = (
            select(User)
            .where(*base_filter)
            .order_by(User.first_name, User.last_name)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await db.execute(query)

        return list(result.scalars().all()), total

    async def create_teacher(
        self,
        db: AsyncSession,
        first_name: str,
        last_name: str,
        email: str,
        phone: str | None = None,
    ) -> User:
        """Create a new teacher account with a temporary password."""
        tenant_id = get_tenant_id()
        email = email.lower().strip()

        # Check if email already exists in this tenant
        existing = await db.execute(
            select(User).where(
                User.tenant_id == tenant_id,
                User.email == email,
                User.deleted_at.is_(None),
            )
        )
        if existing.scalar_one_or_none():
            raise ConflictException("A user with this email already exists")

        temp_password = secrets.token_urlsafe(12)

        user = User(
            tenant_id=tenant_id,
            email=email,
            password_hash=pwd_context.hash(temp_password),
            first_name=first_name.strip(),
            last_name=last_name.strip(),
            phone=phone.strip() if phone else None,
            role=Role.TEACHER.value,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

        # TODO: Send welcome email with password reset link

        return user

    async def update_teacher(
        self,
        db: AsyncSession,
        teacher_id: uuid.UUID,
        first_name: str | None = None,
        last_name: str | None = None,
        email: str | None = None,
        phone: str | None = ...,
    ) -> User:
        """Update a teacher's details."""
        teacher = await self.get_user(db, teacher_id)
        if teacher.role != Role.TEACHER.value:
            raise NotFoundException("Teacher")

        if first_name is not None:
            teacher.first_name = first_name.strip()
        if last_name is not None:
            teacher.last_name = last_name.strip()
        if email is not None:
            email = email.lower().strip()
            if email != teacher.email:
                tenant_id = get_tenant_id()
                existing = await db.execute(
                    select(User).where(
                        User.tenant_id == tenant_id,
                        User.email == email,
                        User.deleted_at.is_(None),
                        User.id != teacher_id,
                    )
                )
                if existing.scalar_one_or_none():
                    raise ConflictException("A user with this email already exists")
                teacher.email = email
        if phone is not ...:
            teacher.phone = phone.strip() if phone else None

        await db.commit()
        await db.refresh(teacher)
        return teacher

    async def deactivate_teacher(
        self,
        db: AsyncSession,
        teacher_id: uuid.UUID,
    ) -> User:
        """Deactivate a teacher account."""
        teacher = await self.get_user(db, teacher_id)
        if teacher.role != Role.TEACHER.value:
            raise NotFoundException("Teacher")
        teacher.is_active = False
        await db.commit()
        await db.refresh(teacher)
        return teacher

    async def activate_teacher(
        self,
        db: AsyncSession,
        teacher_id: uuid.UUID,
    ) -> User:
        """Activate a teacher account."""
        teacher = await self.get_user(db, teacher_id)
        if teacher.role != Role.TEACHER.value:
            raise NotFoundException("Teacher")
        teacher.is_active = True
        await db.commit()
        await db.refresh(teacher)
        return teacher

    async def admin_set_password(
        self,
        db: AsyncSession,
        teacher_id: uuid.UUID,
        new_password: str,
    ) -> User:
        """Admin sets a new password for a teacher."""
        teacher = await self.get_user(db, teacher_id)
        if teacher.role != Role.TEACHER.value:
            raise NotFoundException("Teacher")
        teacher.password_hash = hash_password(new_password)
        await db.commit()
        await db.refresh(teacher)
        return teacher

    async def count_users_by_role(
        self,
        db: AsyncSession,
        role: Role | str,
    ) -> int:
        """Count users with a specific role in the current tenant."""
        tenant_id = get_tenant_id()
        role_value = role.value if isinstance(role, Role) else role

        query = select(func.count(User.id)).where(
            User.tenant_id == tenant_id,
            User.role == role_value,
            User.deleted_at.is_(None),
            User.is_active == True,
        )
        result = await db.execute(query)
        return result.scalar() or 0


def get_user_service() -> UserService:
    """Get user service instance."""
    return UserService()
