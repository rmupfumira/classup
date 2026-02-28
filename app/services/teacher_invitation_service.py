"""Teacher invitation service."""

import logging
import secrets
import string
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Tenant, User
from app.models.teacher_invitation import TeacherInvitation
from app.utils.tenant_context import get_current_user_id, get_tenant_id

logger = logging.getLogger(__name__)
settings = get_settings()


class TeacherInvitationService:
    """Service for managing teacher invitations."""

    def _generate_code(self, length: int = 8) -> str:
        """Generate a random alphanumeric invitation code."""
        alphabet = string.ascii_uppercase + string.digits
        alphabet = alphabet.replace("0", "").replace("O", "").replace("I", "").replace("1", "")
        return "".join(secrets.choice(alphabet) for _ in range(length))

    async def create_invitation(
        self,
        db: AsyncSession,
        email: str,
        first_name: str,
        last_name: str = "",
    ) -> TeacherInvitation:
        """Create a new teacher invitation and return it."""
        tenant_id = get_tenant_id()
        user_id = get_current_user_id()

        email = email.lower().strip()

        # Check if teacher already exists with this email
        existing_user = await db.execute(
            select(User).where(
                User.tenant_id == tenant_id,
                User.email == email,
                User.deleted_at.is_(None),
            )
        )
        if existing_user.scalar_one_or_none():
            raise ValueError("A user with this email already exists")

        # Check for existing pending invitation
        existing_inv = await db.execute(
            select(TeacherInvitation).where(
                and_(
                    TeacherInvitation.tenant_id == tenant_id,
                    TeacherInvitation.email == email,
                    TeacherInvitation.status == "PENDING",
                )
            )
        )
        if existing_inv.scalar_one_or_none():
            raise ValueError("A pending invitation already exists for this email")

        # Generate unique code
        code = self._generate_code()
        while True:
            check = await db.execute(
                select(TeacherInvitation).where(
                    TeacherInvitation.invitation_code == code
                )
            )
            if not check.scalar_one_or_none():
                break
            code = self._generate_code()

        expires_at = datetime.now(timezone.utc) + timedelta(
            days=settings.invitation_code_expiry_days
        )

        invitation = TeacherInvitation(
            tenant_id=tenant_id,
            email=email,
            first_name=first_name.strip(),
            last_name=last_name.strip(),
            invitation_code=code,
            status="PENDING",
            created_by=user_id,
            expires_at=expires_at,
        )

        db.add(invitation)
        await db.commit()
        await db.refresh(invitation)

        logger.info(
            f"Created teacher invitation {invitation.id} for {email}"
        )
        return invitation

    async def get_invitation_by_code(
        self,
        db: AsyncSession,
        code: str,
    ) -> TeacherInvitation | None:
        """Get a teacher invitation by code (no tenant context required)."""
        result = await db.execute(
            select(TeacherInvitation).where(
                TeacherInvitation.invitation_code == code.upper()
            )
        )
        return result.scalar_one_or_none()

    async def verify_invitation(
        self,
        db: AsyncSession,
        code: str,
        email: str,
    ) -> dict:
        """Verify a teacher invitation code and email match."""
        invitation = await self.get_invitation_by_code(db, code)

        if not invitation:
            return {"valid": False, "message": "Invalid invitation code"}

        if invitation.email.lower() != email.lower():
            return {"valid": False, "message": "Email does not match invitation"}

        if invitation.status == "ACCEPTED":
            return {"valid": False, "message": "This invitation has already been used"}

        if invitation.status == "EXPIRED" or invitation.expires_at < datetime.now(
            timezone.utc
        ):
            if invitation.status != "EXPIRED":
                invitation.status = "EXPIRED"
                await db.commit()
            return {"valid": False, "message": "This invitation has expired"}

        tenant = await db.get(Tenant, invitation.tenant_id)

        return {
            "valid": True,
            "message": "Invitation is valid",
            "school_name": tenant.name if tenant else None,
            "email": invitation.email,
            "first_name": invitation.first_name,
            "last_name": invitation.last_name,
            "role": "TEACHER",
        }

    async def accept_invitation(
        self,
        db: AsyncSession,
        code: str,
        teacher_id: UUID,
    ) -> bool:
        """Mark a teacher invitation as accepted."""
        invitation = await self.get_invitation_by_code(db, code)
        if not invitation or invitation.status != "PENDING":
            return False

        invitation.status = "ACCEPTED"
        invitation.accepted_at = datetime.now(timezone.utc)

        await db.commit()
        logger.info(
            f"Teacher invitation {invitation.id} accepted by user {teacher_id}"
        )
        return True

    async def list_invitations(
        self,
        db: AsyncSession,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[TeacherInvitation], int]:
        """List teacher invitations with optional filters."""
        tenant_id = get_tenant_id()

        query = select(TeacherInvitation).where(
            TeacherInvitation.tenant_id == tenant_id
        )

        if status:
            query = query.where(TeacherInvitation.status == status)

        count_query = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_query)).scalar() or 0

        query = query.order_by(TeacherInvitation.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(query)
        invitations = list(result.scalars().all())

        return invitations, total

    async def cancel_invitation(
        self,
        db: AsyncSession,
        invitation_id: UUID,
    ) -> bool:
        """Cancel a teacher invitation."""
        tenant_id = get_tenant_id()

        result = await db.execute(
            select(TeacherInvitation).where(
                and_(
                    TeacherInvitation.id == invitation_id,
                    TeacherInvitation.tenant_id == tenant_id,
                )
            )
        )
        invitation = result.scalar_one_or_none()
        if not invitation:
            return False

        await db.delete(invitation)
        await db.commit()
        logger.info(f"Teacher invitation {invitation_id} cancelled")
        return True

    async def resend_invitation(
        self,
        db: AsyncSession,
        invitation_id: UUID,
    ) -> TeacherInvitation | None:
        """Resend a teacher invitation with a new code."""
        tenant_id = get_tenant_id()

        result = await db.execute(
            select(TeacherInvitation).where(
                and_(
                    TeacherInvitation.id == invitation_id,
                    TeacherInvitation.tenant_id == tenant_id,
                )
            )
        )
        invitation = result.scalar_one_or_none()
        if not invitation:
            return None

        if invitation.status == "ACCEPTED":
            raise ValueError("Cannot resend an already accepted invitation")

        # Reset status to PENDING if it was expired
        invitation.status = "PENDING"

        invitation.invitation_code = self._generate_code()
        invitation.expires_at = datetime.now(timezone.utc) + timedelta(
            days=settings.invitation_code_expiry_days
        )

        await db.commit()
        await db.refresh(invitation)

        logger.info(f"Teacher invitation {invitation_id} resent with new code")
        return invitation


_teacher_invitation_service: TeacherInvitationService | None = None


def get_teacher_invitation_service() -> TeacherInvitationService:
    """Get the teacher invitation service singleton."""
    global _teacher_invitation_service
    if _teacher_invitation_service is None:
        _teacher_invitation_service = TeacherInvitationService()
    return _teacher_invitation_service
