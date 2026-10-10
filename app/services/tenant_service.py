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
        country: str | None = None,
    ) -> Tenant:
        """Create a new tenant.

        ``country`` is the ISO-3166 alpha-2 jurisdiction code (ZW, ZA,
        ...). It drives currency (via jurisdiction_service) and which
        curriculum packs are offered. Stored on
        ``tenant.settings.country``; when omitted, the platform
        default's country applies.
        """
        from app.utils.reserved_slugs import is_reserved_slug

        # Generate slug if not provided
        if not slug:
            slug = self._generate_slug(name)

        # Reject reserved slugs (clashes with app routes)
        if is_reserved_slug(slug):
            from app.exceptions import ValidationException
            raise ValidationException([
                {"field": "slug", "message": f"'{slug}' is a reserved URL and cannot be used. Pick a different slug."}
            ])

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

        # Get platform defaults (currency, country, language, timezone, ...)
        # so the super admin's choices propagate to new tenants. Lazy import
        # to avoid circular reference: tenant_service is imported during app
        # startup before all services are wired.
        from app.services.platform_service import get_defaults as get_platform_defaults
        platform = await get_platform_defaults(db)

        # Get default settings for education type, seeded with platform values.
        # Country drives terminology (Headmaster vs Principal, etc.) and
        # terms_per_year (3 for ZW, 4 for ZA) — pass it in so defaults
        # arrive correct instead of being patched post-hoc.
        settings = get_default_tenant_settings(
            education_type,
            platform_defaults=platform.to_dict(),
            country_code=country,
        )
        # Explicit country at tenant-create wins over the platform
        # default. (Already resolved inside get_default_tenant_settings,
        # but we keep this line in case a caller passes country alone.)
        if country:
            settings["country"] = country.upper()

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
        await db.flush()  # Flush to get tenant.id

        # Seed default grade levels — country-aware so a Zimbabwe
        # tenant gets ECD A/B + Form 1-6, a South African tenant gets
        # Grade RRR/RR/R + Grade 1-12, etc.
        from app.services.grade_level_service import get_grade_level_service
        grade_level_service = get_grade_level_service()
        await grade_level_service.seed_grade_levels_for_tenant(
            db, tenant.id, education_type.value,
            country_code=settings.get("country"),
        )

        # Seed default chart of accounts + a default bank account so the
        # accounting module is usable from day 1.
        from app.services.accounting_service import get_accounting_service
        accounting_service = get_accounting_service()
        await accounting_service.seed_defaults_for_tenant(db, tenant.id)

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
        slug: str | None = None,
        is_active: bool | None = None,
        settings: dict | None = None,
    ) -> Tenant:
        """Update a tenant."""
        from app.exceptions import ValidationException
        from app.utils.reserved_slugs import is_reserved_slug

        tenant = await self.get_tenant(db, tenant_id)

        if name is not None:
            tenant.name = name
        if email is not None:
            tenant.email = email
        if phone is not None:
            tenant.phone = phone
        if address is not None:
            tenant.address = address
        if slug is not None and slug != tenant.slug:
            # Slug change: validate and ensure uniqueness
            new_slug = slug.strip().lower()
            if not new_slug:
                raise ValidationException([
                    {"field": "slug", "message": "Slug cannot be empty."}
                ])
            import re
            if not re.fullmatch(r"[a-z0-9-]+", new_slug):
                raise ValidationException([
                    {"field": "slug", "message": "Slug may only contain lowercase letters, numbers, and hyphens."}
                ])
            if is_reserved_slug(new_slug):
                raise ValidationException([
                    {"field": "slug", "message": f"'{new_slug}' is a reserved URL and cannot be used."}
                ])
            existing = await self.get_tenant_by_slug(db, new_slug)
            if existing and existing.id != tenant.id:
                raise ValidationException([
                    {"field": "slug", "message": f"Slug '{new_slug}' is already taken by another tenant."}
                ])
            tenant.slug = new_slug
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

    # ------------------------------------------------------------------
    # Permanent (hard) tenant deletion + preview
    # ------------------------------------------------------------------
    # Super admin's "Delete Tenant" is a full purge: the Tenant row is
    # physically dropped, DB cascades on tenant_id FKs wipe every
    # child table (users, students, classes, invoices, reports,
    # attendance, events, timetable, invitations, etc.), and the
    # tenant's slug + every parent email + every staff email become
    # immediately reusable on the platform.
    #
    # Rows with ``ondelete='SET NULL'`` on their tenant_id FK survive
    # with the link anonymised — currently that's audit_logs,
    # whatsapp_inbound/outbound, and ai_tool_calls. Preserved for
    # cross-tenant forensic + telemetry history.

    async def preview_tenant_deletion(
        self, db: AsyncSession, tenant_id: uuid.UUID,
    ) -> dict:
        """Return exactly what deleting this tenant will purge.

        Powers the super-admin confirmation modal — nothing is written.
        Counts the biggest-visibility entities (students, parents,
        teachers, admins, classes, invoices, payments, reports,
        events, invitations). The admin sees them before confirming.
        """
        from app.models import (
            AttendanceRecord, BillingInvoice, BillingPayment,
            DailyReport, Message, ParentInvitation, SchoolClass,
            SchoolEvent, Student,
        )
        from app.models.subscription import TenantSubscription
        from app.models.teacher_invitation import TeacherInvitation
        from app.models.user import Role as _Role

        tenant = await self.get_tenant(db, tenant_id)
        if not tenant:
            return {}

        async def _count(stmt) -> int:
            return int((await db.execute(stmt)).scalar() or 0)

        counts: dict[str, int] = {}

        counts["students"] = await _count(
            select(func.count(Student.id)).where(Student.tenant_id == tenant_id)
        )
        counts["parents"] = await _count(
            select(func.count(User.id)).where(
                User.tenant_id == tenant_id,
                User.role == _Role.PARENT.value,
            )
        )
        counts["teachers"] = await _count(
            select(func.count(User.id)).where(
                User.tenant_id == tenant_id,
                User.role == _Role.TEACHER.value,
            )
        )
        counts["school_admins"] = await _count(
            select(func.count(User.id)).where(
                User.tenant_id == tenant_id,
                User.role == _Role.SCHOOL_ADMIN.value,
            )
        )
        counts["classes"] = await _count(
            select(func.count(SchoolClass.id)).where(
                SchoolClass.tenant_id == tenant_id,
            )
        )
        counts["attendance_records"] = await _count(
            select(func.count(AttendanceRecord.id)).where(
                AttendanceRecord.tenant_id == tenant_id,
            )
        )
        counts["reports"] = await _count(
            select(func.count(DailyReport.id)).where(
                DailyReport.tenant_id == tenant_id,
            )
        )
        counts["invoices"] = await _count(
            select(func.count(BillingInvoice.id)).where(
                BillingInvoice.tenant_id == tenant_id,
            )
        )
        counts["payments"] = await _count(
            select(func.count(BillingPayment.id)).where(
                BillingPayment.tenant_id == tenant_id,
            )
        )
        counts["events"] = await _count(
            select(func.count(SchoolEvent.id)).where(
                SchoolEvent.tenant_id == tenant_id,
            )
        )
        counts["messages"] = await _count(
            select(func.count(Message.id)).where(
                Message.tenant_id == tenant_id,
            )
        )
        counts["pending_parent_invitations"] = await _count(
            select(func.count(ParentInvitation.id)).where(
                ParentInvitation.tenant_id == tenant_id,
            )
        )
        counts["pending_teacher_invitations"] = await _count(
            select(func.count(TeacherInvitation.id)).where(
                TeacherInvitation.tenant_id == tenant_id,
            )
        )
        counts["subscriptions"] = await _count(
            select(func.count(TenantSubscription.id)).where(
                TenantSubscription.tenant_id == tenant_id,
            )
        )

        return {
            "tenant": {
                "id": str(tenant.id),
                "name": tenant.name,
                "slug": tenant.slug,
                "email": tenant.email,
            },
            "counts": counts,
        }

    async def hard_delete_tenant(
        self, db: AsyncSession, tenant_id: uuid.UUID,
    ) -> dict:
        """Permanently remove a tenant + every child row that cascades.

        After this returns:
        - The ``tenants`` row is physically gone.
        - Every table with a ``tenant_id`` FK + ``ondelete='CASCADE'``
          has had its matching rows removed by Postgres: users,
          students, classes, attendance, reports, billing,
          accounting, events, invitations, timetable, webhooks,
          subscriptions, notifications, i18n settings, ...
        - Tables with ``ondelete='SET NULL'`` keep their rows with
          tenant_id = NULL: audit_logs, whatsapp_inbound/outbound,
          ai_tool_calls. Preserves cross-tenant history.
        - The tenant slug + every parent/staff email + phone become
          immediately reusable on the platform (no stale UNIQUE
          index entries, no soft-deleted tombstones to work around).

        **Irreversible.** The caller is expected to have gated this
        on a typed-confirmation UI (super admin only).
        """
        import logging
        logger = logging.getLogger(__name__)

        tenant = await self.get_tenant(db, tenant_id)
        if tenant is None:
            raise ValueError(f"Tenant {tenant_id} not found")
        tenant_name = tenant.name
        tenant_slug = tenant.slug

        await db.delete(tenant)
        await db.commit()

        logger.info(
            "Hard-deleted tenant %s (%s / %s) — all cascading children purged",
            tenant_id, tenant_slug, tenant_name,
        )

        return {
            "tenant_name": tenant_name,
            "tenant_slug": tenant_slug,
        }

    async def delete_tenant(self, db: AsyncSession, tenant_id: uuid.UUID) -> None:
        """Soft delete a tenant and detach it from forward-facing views.

        The tenant row itself soft-deletes (deleted_at set, is_active
        false). The DB-level ``ondelete`` rules on child tables never
        fire — nothing is actually removed — so we explicitly:

        - Cancel any active TenantSubscription so the super-admin
          Subscriptions page doesn't still show the dead tenant as
          TRIALING/ACTIVE and let an admin extend trial on a corpse.
        - Null the ``tenant_id`` on WhatsApp inbound/outbound logs so
          the super-admin Conversations page doesn't still bucket
          their history under the deleted tenant. The rows survive for
          forensic purposes but no longer surface tenant metadata.

        Child tables with their own ``deleted_at`` (students, users,
        classes, invoices, etc.) are left alone — the TenantMiddleware
        + login flow make them unreachable, and leaving them intact
        preserves historical data if the tenant is ever restored.
        """
        from sqlalchemy import update
        from app.models import WhatsAppInboundMessage, WhatsAppOutboundMessage
        from app.models.subscription import TenantSubscription, SubscriptionStatus

        tenant = await self.get_tenant(db, tenant_id)
        now = datetime.utcnow()
        tenant.deleted_at = now
        tenant.is_active = False

        # Cancel active subscriptions tied to this tenant.
        await db.execute(
            update(TenantSubscription)
            .where(
                TenantSubscription.tenant_id == tenant_id,
                TenantSubscription.status != SubscriptionStatus.CANCELLED.value,
            )
            .values(
                status=SubscriptionStatus.CANCELLED.value,
                cancelled_at=now,
            )
        )

        # Detach WhatsApp history so the deleted tenant's name no
        # longer appears in Conversations. The rows themselves stay.
        await db.execute(
            update(WhatsAppInboundMessage)
            .where(WhatsAppInboundMessage.tenant_id == tenant_id)
            .values(tenant_id=None)
        )
        await db.execute(
            update(WhatsAppOutboundMessage)
            .where(WhatsAppOutboundMessage.tenant_id == tenant_id)
            .values(tenant_id=None)
        )

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
