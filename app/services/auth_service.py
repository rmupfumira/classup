"""Authentication service for login, registration, and token management."""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exceptions import (
    ConflictException,
    NotFoundException,
    UnauthorizedException,
    ValidationException,
)
from app.models import ParentInvitation, ParentStudent, User, Role, InvitationStatus, Tenant
from app.models.teacher_invitation import TeacherInvitation
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    RegisterResponse,
    UserProfile,
)


class MultipleTenantsException(Exception):
    """Raised when a login email matches users in multiple tenants and
    the correct one must be chosen by the caller.

    ``tenants`` is a list of ``(slug, name)`` tuples for every tenant
    where the (email, password) pair verified. The UI renders a chooser
    and the next login attempt includes ``tenant_slug``.
    """

    def __init__(self, tenants: list[tuple[str, str]]):
        self.tenants = tenants
        super().__init__(
            f"This email is registered at {len(tenants)} schools. "
            "Pick which one you're signing in to."
        )
from app.utils.security import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    hash_password,
    verify_password,
)


class AuthService:
    """Service for handling authentication operations."""

    async def login(
        self, db: AsyncSession, request: LoginRequest
    ) -> tuple[LoginResponse, User]:
        """Authenticate a user and return tokens.

        Owner directive 2026-10-06: the same email can be registered at
        multiple schools (a parent at two campuses, an admin running
        a group). Resolution:

        - If ``tenant_slug`` is supplied, scope the lookup to that
          tenant. Standard single-row flow.
        - Otherwise, find every User with this email and verify the
          password against each. 0 verified → UnauthorizedException.
          1 verified → log that one in. 2+ verified → raise
          :class:`MultipleTenantsException` so the UI can render a
          school chooser and the caller re-submits with ``tenant_slug``.

        Super admin rows (``tenant_id = NULL``) are included — a super
        admin's email is unique by convention, so they never hit the
        chooser.

        Args:
            db: Database session
            request: Login request with email, password, and optional
                tenant_slug.

        Returns:
            Tuple of (LoginResponse, User)

        Raises:
            UnauthorizedException: If credentials are invalid
            MultipleTenantsException: If the email + password matched
                multiple tenants and the caller must pick one.
        """
        slug = (request.tenant_slug or "").strip() or None

        # Find candidate users. With a tenant_slug, scope to that
        # tenant's row. Without, we might get several rows (same email
        # at multiple schools) and we fan the password-verify across them.
        stmt = (
            select(User)
            .where(
                User.email == request.email,
                User.deleted_at.is_(None),
            )
        )
        if slug:
            stmt = stmt.join(
                Tenant, User.tenant_id == Tenant.id
            ).where(
                Tenant.slug == slug,
                Tenant.deleted_at.is_(None),
            )
        result = await db.execute(stmt)
        candidates = list(result.scalars().all())

        if not candidates:
            raise UnauthorizedException("Invalid email or password")

        matches = [
            u for u in candidates
            if verify_password(request.password, u.password_hash)
        ]

        if not matches:
            raise UnauthorizedException("Invalid email or password")

        if len(matches) > 1:
            # Multiple distinct tenants verified — ask the caller to
            # pick. Load tenant names for the chooser UI. Super admin
            # (no tenant) can't collide with tenant-scoped rows under
            # the UNIQUE(email, tenant_id) constraint, so this list is
            # purely tenant-scoped users.
            tenant_ids = [u.tenant_id for u in matches if u.tenant_id]
            tenant_rows = []
            if tenant_ids:
                tres = await db.execute(
                    select(Tenant).where(Tenant.id.in_(tenant_ids))
                )
                tenant_rows = list(tres.scalars().all())
            by_id = {t.id: t for t in tenant_rows}
            choices = sorted(
                (
                    (by_id[u.tenant_id].slug, by_id[u.tenant_id].name)
                    for u in matches
                    if u.tenant_id and u.tenant_id in by_id
                ),
                key=lambda x: x[1].lower(),
            )
            raise MultipleTenantsException(choices)

        user = matches[0]

        # Check if account is active
        if not user.is_active:
            raise UnauthorizedException("Your account is inactive")

        # Update last login time
        user.last_login_at = datetime.utcnow()
        await db.commit()

        # Generate tokens
        access_token = create_access_token(
            user_id=user.id,
            tenant_id=user.tenant_id,
            role=user.role,
            name=user.full_name,
        )
        refresh_token = create_refresh_token(user_id=user.id)

        response = LoginResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            expires_in=settings.jwt_access_token_expire_minutes * 60,
        )

        return response, user

    async def register_parent(
        self, db: AsyncSession, request: RegisterRequest
    ) -> RegisterResponse:
        """Register a new parent user via invitation code.

        Args:
            db: Database session
            request: Registration request

        Returns:
            RegisterResponse with user details

        Raises:
            ValidationException: If invitation is invalid or expired
            ConflictException: If email already exists
        """
        # Find and validate invitation
        stmt = select(ParentInvitation).where(
            ParentInvitation.invitation_code == request.invitation_code.upper(),
            ParentInvitation.email == request.email,
            ParentInvitation.status == InvitationStatus.PENDING.value,
        )
        result = await db.execute(stmt)
        invitation = result.scalar_one_or_none()

        if not invitation:
            raise ValidationException("Invalid invitation code or email")

        if invitation.is_expired:
            invitation.mark_expired()
            await db.commit()
            raise ValidationException("This invitation has expired")

        # Check if email already exists for this tenant
        existing_user = await self._get_user_by_email(db, request.email, invitation.tenant_id)
        if existing_user:
            raise ConflictException("An account with this email already exists")

        # Create user. ``phone`` is now required on the request (2026-
        # 10-09 redesign); if the parent ticks WhatsApp opt-in, mirror
        # the same number into ``whatsapp_phone`` so the bot can match
        # them from the first inbound. ``whatsapp_opted_in`` and
        # ``email_opted_in`` are the parent's own explicit decisions
        # — the admin's "Suggest WhatsApp opt-in" only pre-ticked the
        # form field.
        user = User(
            tenant_id=invitation.tenant_id,
            email=request.email,
            password_hash=hash_password(request.password),
            first_name=request.first_name,
            last_name=request.last_name,
            phone=request.phone,
            whatsapp_phone=request.phone if request.whatsapp_opt_in else None,
            whatsapp_opted_in=bool(request.whatsapp_opt_in),
            email_opted_in=bool(request.email_opt_in),
            role=Role.PARENT.value,
            is_active=True,
        )
        db.add(user)
        await db.flush()

        # Link parent to student
        parent_student = ParentStudent(
            parent_id=user.id,
            student_id=invitation.student_id,
            relationship_type="PARENT",
            is_primary=True,  # First registered parent is primary
        )
        db.add(parent_student)

        # Mark invitation as accepted
        invitation.mark_accepted()

        await db.commit()

        # Fire-and-forget admin notification — a parent completing
        # signup is a signal that outreach is converting. Each channel
        # is independent and failure never breaks registration.
        try:
            await self._notify_admins_parent_signed_up(db, user, invitation)
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "Failed to notify admins of parent signup (user_id=%s)", user.id,
            )

        return RegisterResponse(
            user_id=user.id,
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
        )

    async def _notify_admins_parent_signed_up(
        self,
        db: AsyncSession,
        parent: User,
        invitation: ParentInvitation,
    ) -> None:
        """Tell every school admin that an invited parent just finished
        signing up. Fires in-app + email + (opted-in) WhatsApp.

        Each channel is independent — a WhatsApp failure never blocks
        the email, an email failure never blocks the in-app notification.
        All exceptions are logged, never raised, so a late crash can't
        undo a successful registration.
        """
        import logging

        from app.models import Student
        from app.models.user import Role
        from app.services.email_service import get_email_service
        from app.services.notification_service import get_notification_service

        logger = logging.getLogger(__name__)
        tenant_id = parent.tenant_id

        # Look up tenant + student for context strings. Any failure here
        # just means we fall back to generic text.
        tenant = await db.get(Tenant, tenant_id)
        tenant_name = tenant.name if tenant else "Your School"
        student_name = "a student"
        try:
            if invitation.student_id:
                student = await db.get(Student, invitation.student_id)
                if student:
                    student_name = f"{student.first_name} {student.last_name}"
        except Exception:
            pass

        parent_name = f"{parent.first_name} {parent.last_name}"
        title = f"Parent signed up: {parent_name}"
        body = (
            f"{parent_name} ({parent.email}) completed signup and is now "
            f"linked to {student_name}."
        )

        # Fetch admins once — used for all three channels.
        admin_result = await db.execute(
            select(User).where(
                User.tenant_id == tenant_id,
                User.role == Role.SCHOOL_ADMIN.value,
                User.is_active == True,
                User.deleted_at.is_(None),
            )
        )
        admins = list(admin_result.scalars().all())
        if not admins:
            return

        # 1) In-app notifications. Pass tenant_id explicitly — this
        # method runs on a public registration request so the tenant
        # contextvar isn't set; create_bulk_notifications would
        # otherwise crash with TenantContextError.
        try:
            notification_service = get_notification_service()
            await notification_service.create_bulk_notifications(
                db=db,
                user_ids=[a.id for a in admins],
                title=title,
                body=body,
                notification_type="NEW_PARENT_SIGNED_UP",
                reference_type="user",
                reference_id=parent.id,
                tenant_id=tenant_id,
            )
        except Exception:
            logger.exception("In-app notify failed for parent signup")

        # 2) Email broadcast — reuse the generic admin notification helper.
        try:
            email_service = get_email_service()
            await email_service.notify_admins(
                db=db,
                tenant_id=tenant_id,
                notification_type="NEW_PARENT_SIGNED_UP",
                title=title,
                body=body,
                action_url="/admin/parents",
            )
        except Exception:
            logger.exception("Email notify failed for parent signup")

        # 3) WhatsApp — only to opted-in admins. Best effort per admin.
        try:
            from app.services import parent_notifier
            for admin in admins:
                try:
                    if await parent_notifier._can_notify_whatsapp(db, admin):
                        from app.services.whatsapp_service import get_whatsapp_service
                        wa = get_whatsapp_service()
                        if wa and admin.whatsapp_phone:
                            await wa.send_text_message(
                                to=admin.whatsapp_phone,
                                text=f"{title}\n\n{body}",
                            )
                except Exception:
                    logger.exception(
                        "WhatsApp notify failed for admin %s", admin.id,
                    )
        except Exception:
            logger.exception("WhatsApp notify loop failed")

    async def register_teacher(
        self, db: AsyncSession, request: RegisterRequest
    ) -> RegisterResponse:
        """Register a new teacher user via invitation code.

        Args:
            db: Database session
            request: Registration request

        Returns:
            RegisterResponse with user details

        Raises:
            ValidationException: If invitation is invalid or expired
            ConflictException: If email already exists
        """
        # Find and validate teacher invitation
        stmt = select(TeacherInvitation).where(
            TeacherInvitation.invitation_code == request.invitation_code.upper(),
            TeacherInvitation.email == request.email,
            TeacherInvitation.status == "PENDING",
        )
        result = await db.execute(stmt)
        invitation = result.scalar_one_or_none()

        if not invitation:
            raise ValidationException("Invalid invitation code or email")

        if invitation.is_expired:
            invitation.mark_expired()
            await db.commit()
            raise ValidationException("This invitation has expired")

        # Check if email already exists for this tenant
        existing_user = await self._get_user_by_email(
            db, request.email, invitation.tenant_id
        )
        if existing_user:
            raise ConflictException("An account with this email already exists")

        # Create teacher user
        user = User(
            tenant_id=invitation.tenant_id,
            email=request.email,
            password_hash=hash_password(request.password),
            first_name=request.first_name,
            last_name=request.last_name,
            phone=request.phone,
            role=Role.TEACHER.value,
            is_active=True,
        )
        db.add(user)
        await db.flush()

        # Mark invitation as accepted
        invitation.mark_accepted()

        await db.commit()

        # Send email notifications
        try:
            from app.services.email_service import get_email_service
            from app.models import Tenant

            email_service = get_email_service()
            teacher_name = f"{user.first_name} {user.last_name}"

            # Get tenant name for emails
            tenant = await db.get(Tenant, invitation.tenant_id)
            tenant_name = tenant.name if tenant else "Your School"

            # Welcome email to the teacher
            await email_service.send_welcome_email(
                to=user.email,
                user_name=user.first_name,
                tenant_name=tenant_name,
                login_url=f"{settings.app_base_url}/login",
            )

            # Notify admins about new teacher registration
            await email_service.notify_admins(
                db=db,
                tenant_id=invitation.tenant_id,
                notification_type="TEACHER_ADDED",
                title=f"New Teacher Registered: {teacher_name}",
                body=(
                    f"{teacher_name} ({user.email}) has accepted their invitation "
                    f"and registered as a teacher. You can now assign them to classes."
                ),
                action_url=f"{settings.app_base_url}/teachers",
            )
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "Failed to send email notifications for new teacher"
            )

        return RegisterResponse(
            user_id=user.id,
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
        )

    async def refresh_token(
        self, db: AsyncSession, refresh_token: str
    ) -> LoginResponse:
        """Refresh an access token using a refresh token.

        Args:
            db: Database session
            refresh_token: The refresh token

        Returns:
            LoginResponse with new tokens

        Raises:
            UnauthorizedException: If refresh token is invalid
        """
        # Decode and validate refresh token
        payload = decode_refresh_token(refresh_token)
        if not payload:
            raise UnauthorizedException("Invalid or expired refresh token")

        user_id = uuid.UUID(payload["sub"])

        # Get user from database
        stmt = select(User).where(User.id == user_id, User.deleted_at.is_(None))
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            raise UnauthorizedException("User not found")

        if not user.is_active:
            raise UnauthorizedException("Your account is inactive")

        # Generate new tokens
        new_access_token = create_access_token(
            user_id=user.id,
            tenant_id=user.tenant_id,
            role=user.role,
            name=user.full_name,
        )
        new_refresh_token = create_refresh_token(user_id=user.id)

        return LoginResponse(
            access_token=new_access_token,
            refresh_token=new_refresh_token,
            token_type="bearer",
            expires_in=settings.jwt_access_token_expire_minutes * 60,
        )

    async def get_current_user(self, db: AsyncSession, user_id: uuid.UUID) -> User:
        """Get the current user by ID.

        Args:
            db: Database session
            user_id: User UUID

        Returns:
            User object

        Raises:
            NotFoundException: If user not found
        """
        stmt = select(User).where(User.id == user_id, User.deleted_at.is_(None))
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            raise NotFoundException("User")

        return user

    async def verify_invitation(
        self, db: AsyncSession, code: str, email: str
    ) -> tuple[bool, ParentInvitation | None]:
        """Verify an invitation code and email combination.

        Args:
            db: Database session
            code: Invitation code
            email: Email address

        Returns:
            Tuple of (is_valid, invitation or None)
        """
        stmt = select(ParentInvitation).where(
            ParentInvitation.invitation_code == code.upper(),
            ParentInvitation.email == email,
            ParentInvitation.status == InvitationStatus.PENDING.value,
        )
        result = await db.execute(stmt)
        invitation = result.scalar_one_or_none()

        if not invitation:
            return False, None

        if invitation.is_expired:
            invitation.mark_expired()
            await db.commit()
            return False, None

        return True, invitation

    async def _get_user_by_email(
        self, db: AsyncSession, email: str, tenant_id: uuid.UUID | None = None
    ) -> User | None:
        """Get a user by email and optionally tenant ID.

        Args:
            db: Database session
            email: Email address
            tenant_id: Optional tenant ID

        Returns:
            User or None
        """
        stmt = select(User).where(User.email == email, User.deleted_at.is_(None))

        if tenant_id:
            stmt = stmt.where(User.tenant_id == tenant_id)

        result = await db.execute(stmt)
        return result.scalar_one_or_none()


# Singleton instance
_auth_service: AuthService | None = None


def get_auth_service() -> AuthService:
    """Get the auth service singleton."""
    global _auth_service
    if _auth_service is None:
        _auth_service = AuthService()
    return _auth_service
