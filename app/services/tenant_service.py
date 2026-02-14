"""Tenant service for CRUD operations (Super Admin only)."""

import re
import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.exceptions import ConflictException, NotFoundException
from app.models import Tenant, User
from app.models.tenant import EducationType, get_default_tenant_settings
from app.models.user import Role
from app.utils.security import hash_password


class TenantService:
    """Service for managing tenants (schools/organizations)."""

    async def get_tenants(
        self,
        db: AsyncSession,
        is_active: bool | None = None,
        education_type: str | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Tenant], int]:
        """Get list of tenants with optional filters (Super Admin only)."""
        query = select(Tenant).where(Tenant.deleted_at.is_(None))

        # Apply filters
        if is_active is not None:
            query = query.where(Tenant.is_active == is_active)
        if education_type:
            query = query.where(Tenant.education_type == education_type)
        if search:
            search_term = f"%{search}%"
            query = query.where(
                (Tenant.name.ilike(search_term))
                | (Tenant.email.ilike(search_term))
                | (Tenant.slug.ilike(search_term))
            )

        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_query)).scalar() or 0

        # Apply pagination and ordering
        query = query.order_by(Tenant.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(query)
        tenants = list(result.scalars().all())

        return tenants, total

    async def get_tenant(self, db: AsyncSession, tenant_id: uuid.UUID) -> Tenant:
        """Get a tenant by ID."""
        query = select(Tenant).where(
            Tenant.id == tenant_id,
            Tenant.deleted_at.is_(None),
        )
        result = await db.execute(query)
        tenant = result.scalar_one_or_none()

        if not tenant:
            raise NotFoundException("Tenant")

        return tenant

    async def get_tenant_by_slug(self, db: AsyncSession, slug: str) -> Tenant | None:
        """Get a tenant by slug."""
        query = select(Tenant).where(
            Tenant.slug == slug,
            Tenant.deleted_at.is_(None),
        )
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def get_tenant_stats(
        self, db: AsyncSession, tenant_id: uuid.UUID
    ) -> dict:
        """Get statistics for a specific tenant."""
        from app.models import Student, SchoolClass

        # Count users by role
        user_counts = await db.execute(
            select(User.role, func.count(User.id))
            .where(User.tenant_id == tenant_id, User.deleted_at.is_(None))
            .group_by(User.role)
        )
        users_by_role = dict(user_counts.all())

        # Count students
        student_count = await db.execute(
            select(func.count(Student.id)).where(
                Student.tenant_id == tenant_id,
                Student.deleted_at.is_(None),
            )
        )

        # Count classes
        class_count = await db.execute(
            select(func.count(SchoolClass.id)).where(
                SchoolClass.tenant_id == tenant_id,
                SchoolClass.deleted_at.is_(None),
            )
        )

        return {
            "total_users": sum(users_by_role.values()),
            "users_by_role": users_by_role,
            "total_teachers": users_by_role.get("TEACHER", 0),
            "total_students": student_count.scalar() or 0,
            "total_classes": class_count.scalar() or 0,
        }

    async def create_tenant(
        self,
        db: AsyncSession,
        name: str,
        email: str,
        education_type: EducationType,
        phone: str | None = None,
        address: str | None = None,
        slug: str | None = None,
    ) -> Tenant:
        """Create a new tenant."""
        # Generate slug if not provided
        if not slug:
            slug = self._generate_slug(name)

        # Check if slug already exists
        existing = await self.get_tenant_by_slug(db, slug)
        if existing:
            # Append a number to make it unique
            base_slug = slug
            counter = 1
            while existing:
                slug = f"{base_slug}-{counter}"
                existing = await self.get_tenant_by_slug(db, slug)
                counter += 1

        # Get default settings for education type
        settings = get_default_tenant_settings(education_type)

        tenant = Tenant(
            name=name,
            slug=slug,
            email=email,
            phone=phone,
            address=address,
            education_type=education_type.value,
            settings=settings,
            is_active=True,
            onboarding_completed=False,
        )

        db.add(tenant)
        await db.commit()
        await db.refresh(tenant)

        return tenant

    async def update_tenant(
        self,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        name: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        address: str | None = None,
        is_active: bool | None = None,
        settings: dict | None = None,
    ) -> Tenant:
        """Update a tenant."""
        tenant = await self.get_tenant(db, tenant_id)

        if name is not None:
            tenant.name = name
        if email is not None:
            tenant.email = email
        if phone is not None:
            tenant.phone = phone
        if address is not None:
            tenant.address = address
        if is_active is not None:
            tenant.is_active = is_active
        if settings is not None:
            # Merge settings instead of replacing
            current_settings = tenant.settings.copy()
            current_settings.update(settings)
            tenant.settings = current_settings

        await db.commit()
        await db.refresh(tenant)

        return tenant

    async def delete_tenant(self, db: AsyncSession, tenant_id: uuid.UUID) -> None:
        """Soft delete a tenant."""
        tenant = await self.get_tenant(db, tenant_id)
        tenant.deleted_at = datetime.utcnow()
        tenant.is_active = False
        await db.commit()

    async def get_platform_stats(self, db: AsyncSession) -> dict:
        """Get platform-wide statistics (Super Admin dashboard)."""
        from app.models import Student, SchoolClass

        # Count tenants
        tenant_count = await db.execute(
            select(func.count(Tenant.id)).where(Tenant.deleted_at.is_(None))
        )
        active_tenant_count = await db.execute(
            select(func.count(Tenant.id)).where(
                Tenant.deleted_at.is_(None),
                Tenant.is_active == True,
            )
        )

        # Count by education type
        education_type_counts = await db.execute(
            select(Tenant.education_type, func.count(Tenant.id))
            .where(Tenant.deleted_at.is_(None))
            .group_by(Tenant.education_type)
        )

        # Total users across all tenants
        total_users = await db.execute(
            select(func.count(User.id)).where(User.deleted_at.is_(None))
        )

        # Total students across all tenants
        total_students = await db.execute(
            select(func.count(Student.id)).where(Student.deleted_at.is_(None))
        )

        # Recent tenants
        recent_tenants_query = (
            select(Tenant)
            .where(Tenant.deleted_at.is_(None))
            .order_by(Tenant.created_at.desc())
            .limit(5)
        )
        recent_tenants_result = await db.execute(recent_tenants_query)
        recent_tenants = list(recent_tenants_result.scalars().all())

        return {
            "total_tenants": tenant_count.scalar() or 0,
            "active_tenants": active_tenant_count.scalar() or 0,
            "tenants_by_type": dict(education_type_counts.all()),
            "total_users": total_users.scalar() or 0,
            "total_students": total_students.scalar() or 0,
            "recent_tenants": recent_tenants,
        }

    async def get_tenant_admins(
        self, db: AsyncSession, tenant_id: uuid.UUID
    ) -> list[User]:
        """Get all admin users for a tenant."""
        query = select(User).where(
            User.tenant_id == tenant_id,
            User.role == Role.SCHOOL_ADMIN.value,
            User.deleted_at.is_(None),
        ).order_by(User.created_at.asc())

        result = await db.execute(query)
        return list(result.scalars().all())

    async def create_tenant_admin(
        self,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        email: str,
        password: str,
        first_name: str,
        last_name: str,
        phone: str | None = None,
    ) -> User:
        """Create an admin user for a tenant."""
        # Check tenant exists
        tenant = await self.get_tenant(db, tenant_id)

        # Check if email already exists for this tenant
        existing = await db.execute(
            select(User).where(
                User.email == email,
                User.tenant_id == tenant_id,
                User.deleted_at.is_(None),
            )
        )
        if existing.scalar_one_or_none():
            raise ConflictException("A user with this email already exists for this tenant")

        user = User(
            tenant_id=tenant_id,
            email=email,
            password_hash=hash_password(password),
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            role=Role.SCHOOL_ADMIN.value,
            is_active=True,
        )

        db.add(user)
        await db.commit()
        await db.refresh(user)

        return user

    def _generate_slug(self, name: str) -> str:
        """Generate a URL-safe slug from name."""
        # Convert to lowercase and replace spaces/special chars with hyphens
        slug = name.lower()
        slug = re.sub(r"[^a-z0-9]+", "-", slug)
        slug = slug.strip("-")
        return slug


# Singleton instance
_tenant_service: TenantService | None = None


def get_tenant_service() -> TenantService:
    """Get the tenant service singleton."""
    global _tenant_service
    if _tenant_service is None:
        _tenant_service = TenantService()
    return _tenant_service
