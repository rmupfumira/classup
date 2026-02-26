"""Parent invitation service."""

import logging
import secrets
import string
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import ParentInvitation, Student, Tenant, User
from app.utils.tenant_context import get_current_user_id, get_tenant_id

logger = logging.getLogger(__name__)
settings = get_settings()


class InvitationService:
    """Service for managing parent invitations."""

    def _generate_code(self, length: int = 8) -> str:
        """Generate a random alphanumeric invitation code."""
        alphabet = string.ascii_uppercase + string.digits
        # Remove ambiguous characters
        alphabet = alphabet.replace("0", "").replace("O", "").replace("I", "").replace("1", "")
        return "".join(secrets.choice(alphabet) for _ in range(length))

    async def create_invitation(
        self,
        db: AsyncSession,
        student_id: UUID,
        email: str,
        first_name: str = "",
        last_name: str = "",
    ) -> ParentInvitation:
        """Create a new parent invitation."""
        tenant_id = get_tenant_id()
        user_id = get_current_user_id()

        # Verify student exists and belongs to tenant
        student = await db.get(Student, student_id)
        if not student or student.tenant_id != tenant_id:
            raise ValueError("Student not found")

        # Check for existing pending invitation for this email/student
        existing = await db.execute(
            select(ParentInvitation).where(
                and_(
                    ParentInvitation.tenant_id == tenant_id,
                    ParentInvitation.student_id == student_id,
                    ParentInvitation.email == email.lower(),
                    ParentInvitation.status == "PENDING",
                )
            )
        )
        if existing.scalar_one_or_none():
            raise ValueError("An invitation already exists for this email and student")

        # Generate unique code
        code = self._generate_code()
        while True:
            check = await db.execute(
                select(ParentInvitation).where(ParentInvitation.invitation_code == code)
            )
            if not check.scalar_one_or_none():
                break
            code = self._generate_code()

        # Calculate expiry
        expiry_days = settings.invitation_code_expiry_days
        expires_at = datetime.now(timezone.utc) + timedelta(days=expiry_days)

        invitation = ParentInvitation(
            tenant_id=tenant_id,
            student_id=student_id,
            email=email.lower(),
            first_name=first_name,
            last_name=last_name,
            invitation_code=code,
            status="PENDING",
            created_by=user_id,
            expires_at=expires_at,
        )

        db.add(invitation)
        await db.commit()
        await db.refresh(invitation)

        logger.info(f"Created invitation {invitation.id} for {email} to student {student_id}")
        return invitation

    async def get_invitation(
        self,
        db: AsyncSession,
        invitation_id: UUID,
    ) -> ParentInvitation | None:
        """Get an invitation by ID."""
        tenant_id = get_tenant_id()

        result = await db.execute(
            select(ParentInvitation).where(
                and_(
                    ParentInvitation.id == invitation_id,
                    ParentInvitation.tenant_id == tenant_id,
                )
            )
        )
        return result.scalar_one_or_none()

    async def get_invitation_by_code(
        self,
        db: AsyncSession,
        code: str,
    ) -> ParentInvitation | None:
        """Get an invitation by code (no tenant context required)."""
        result = await db.execute(
            select(ParentInvitation).where(ParentInvitation.invitation_code == code.upper())
        )
        return result.scalar_one_or_none()

    async def verify_invitation(
        self,
        db: AsyncSession,
        code: str,
        email: str,
    ) -> dict:
        """Verify an invitation code and email match."""
        invitation = await self.get_invitation_by_code(db, code)

        if not invitation:
            return {"valid": False, "message": "Invalid invitation code"}

        if invitation.email.lower() != email.lower():
            return {"valid": False, "message": "Email does not match invitation"}

        if invitation.status == "ACCEPTED":
            return {"valid": False, "message": "This invitation has already been used"}

        if invitation.status == "EXPIRED" or invitation.expires_at < datetime.now(timezone.utc):
            # Mark as expired if not already
            if invitation.status != "EXPIRED":
                invitation.status = "EXPIRED"
                await db.commit()
            return {"valid": False, "message": "This invitation has expired"}

        # Get student and tenant info
        student = await db.get(Student, invitation.student_id)
        tenant = await db.get(Tenant, invitation.tenant_id)

        return {
            "valid": True,
            "message": "Invitation is valid",
            "student_name": f"{student.first_name} {student.last_name}" if student else None,
            "school_name": tenant.name if tenant else None,
            "email": invitation.email,
            "first_name": invitation.first_name,
            "last_name": invitation.last_name,
        }

    async def accept_invitation(
        self,
        db: AsyncSession,
        code: str,
        parent_id: UUID,
    ) -> bool:
        """Mark an invitation as accepted and link parent to student."""
        from app.models import ParentStudent

        invitation = await self.get_invitation_by_code(db, code)
        if not invitation or invitation.status != "PENDING":
            return False

        # Update invitation status
        invitation.status = "ACCEPTED"
        invitation.accepted_at = datetime.now(timezone.utc)

        # Create parent-student relationship
        parent_student = ParentStudent(
            parent_id=parent_id,
            student_id=invitation.student_id,
            relationship="PARENT",
            is_primary=True,
        )
        db.add(parent_student)

        await db.commit()
        logger.info(f"Invitation {invitation.id} accepted by parent {parent_id}")
        return True

    async def cancel_invitation(
        self,
        db: AsyncSession,
        invitation_id: UUID,
    ) -> bool:
        """Cancel (delete) an invitation."""
        invitation = await self.get_invitation(db, invitation_id)
        if not invitation:
            return False

        await db.delete(invitation)
        await db.commit()
        logger.info(f"Invitation {invitation_id} cancelled")
        return True

    async def resend_invitation(
        self,
        db: AsyncSession,
        invitation_id: UUID,
    ) -> ParentInvitation | None:
        """Resend an invitation (generate new code and extend expiry)."""
        invitation = await self.get_invitation(db, invitation_id)
        if not invitation:
            return None

        if invitation.status != "PENDING":
            raise ValueError("Can only resend pending invitations")

        # Generate new code
        invitation.invitation_code = self._generate_code()
        invitation.expires_at = datetime.now(timezone.utc) + timedelta(
            days=settings.invitation_code_expiry_days
        )

        await db.commit()
        await db.refresh(invitation)

        logger.info(f"Invitation {invitation_id} resent with new code")
        return invitation

    async def list_invitations(
        self,
        db: AsyncSession,
        status: str | None = None,
        student_id: UUID | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[ParentInvitation], int]:
        """List invitations with optional filters."""
        tenant_id = get_tenant_id()

        query = select(ParentInvitation).where(ParentInvitation.tenant_id == tenant_id)

        if status:
            query = query.where(ParentInvitation.status == status)

        if student_id:
            query = query.where(ParentInvitation.student_id == student_id)

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_query)).scalar() or 0

        # Apply pagination and ordering
        query = query.order_by(ParentInvitation.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(query)
        invitations = list(result.scalars().all())

        return invitations, total

    async def expire_old_invitations(self, db: AsyncSession) -> int:
        """Mark expired invitations as EXPIRED. Returns count of updated."""
        from sqlalchemy import update

        result = await db.execute(
            update(ParentInvitation)
            .where(
                and_(
                    ParentInvitation.status == "PENDING",
                    ParentInvitation.expires_at < datetime.now(timezone.utc),
                )
            )
            .values(status="EXPIRED")
        )
        await db.commit()
        return result.rowcount


# Singleton instance
_invitation_service: InvitationService | None = None


def get_invitation_service() -> InvitationService:
    """Get the invitation service singleton."""
    global _invitation_service
    if _invitation_service is None:
        _invitation_service = InvitationService()
    return _invitation_service
