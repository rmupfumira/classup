"""User service for CRUD operations."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundException
from app.models import User
from app.models.user import Role
from app.utils.tenant_context import get_tenant_id


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


def get_user_service() -> UserService:
    """Get user service instance."""
    return UserService()
