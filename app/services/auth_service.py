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
from app.models import ParentInvitation, ParentStudent, User, Role, InvitationStatus
from app.models.teacher_invitation import TeacherInvitation
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    RegisterResponse,
    UserProfile,
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

        Args:
            db: Database session
            request: Login request with email and password

        Returns:
            Tuple of (LoginResponse, User)

        Raises:
            UnauthorizedException: If credentials are invalid
        """
        # Find user by email
        stmt = select(User).where(
            User.email == request.email,
            User.deleted_at.is_(None),
        )
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            raise UnauthorizedException("Invalid email or password")

        # Verify password
        if not verify_password(request.password, user.password_hash):
            raise UnauthorizedException("Invalid email or password")

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

        # Create user
        user = User(
            tenant_id=invitation.tenant_id,
            email=request.email,
            password_hash=hash_password(request.password),
            first_name=request.first_name,
            last_name=request.last_name,
            phone=request.phone,
            role=Role.PARENT.value,
            is_active=True,
        )
        db.add(user)
        await db.flush()

        # Link parent to student
        parent_student = ParentStudent(
            parent_id=user.id,
            student_id=invitation.student_id,
            relationship="PARENT",
            is_primary=True,  # First registered parent is primary
        )
        db.add(parent_student)

        # Mark invitation as accepted
        invitation.mark_accepted()

        await db.commit()

        return RegisterResponse(
            user_id=user.id,
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
        )

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
