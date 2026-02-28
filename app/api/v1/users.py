"""User API endpoints."""

import logging
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import Tenant
from app.schemas.common import APIResponse
from app.services.email_service import get_email_service
from app.services.teacher_invitation_service import get_teacher_invitation_service
from app.services.user_service import get_user_service
from app.utils.permissions import require_role
from app.utils.security import create_password_reset_token
from app.utils.tenant_context import get_tenant_id

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter()


class InviteTeacherRequest(BaseModel):
    first_name: str
    last_name: str = ""
    email: EmailStr


class SetPasswordRequest(BaseModel):
    password: str = Field(..., min_length=8)


@router.post("/teachers/invite")
@require_role("SCHOOL_ADMIN")
async def invite_teacher(
    data: InviteTeacherRequest,
    db: AsyncSession = Depends(get_db),
):
    """Invite a teacher by email. Sends an invitation email with a registration code."""
    invitation_service = get_teacher_invitation_service()

    try:
        invitation = await invitation_service.create_invitation(
            db,
            email=data.email,
            first_name=data.first_name,
            last_name=data.last_name,
        )
    except ValueError as e:
        return APIResponse(
            status="error",
            message=str(e),
        )

    # Send invitation email
    tenant = await db.get(Tenant, get_tenant_id())
    tenant_name = tenant.name if tenant else "Your School"
    register_url = (
        f"{settings.app_base_url}/register/teacher?code={invitation.invitation_code}"
    )

    email_service = get_email_service()
    try:
        await email_service.send_teacher_invitation(
            to=invitation.email,
            tenant_name=tenant_name,
            teacher_name=data.first_name,
            invitation_code=invitation.invitation_code,
            register_url=register_url,
            expires_in_days=settings.invitation_code_expiry_days,
        )
    except Exception:
        logger.exception("Failed to send teacher invitation email")

    return APIResponse(
        status="success",
        message=f"Invitation sent to {invitation.email}",
        data={
            "id": str(invitation.id),
            "email": invitation.email,
            "invitation_code": invitation.invitation_code,
            "status": invitation.status,
        },
    )


@router.delete("/teachers/invitations/{invitation_id}")
@require_role("SCHOOL_ADMIN")
async def cancel_teacher_invitation(
    invitation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Cancel a pending teacher invitation."""
    invitation_service = get_teacher_invitation_service()
    success = await invitation_service.cancel_invitation(db, invitation_id)
    if not success:
        return APIResponse(status="error", message="Invitation not found")

    return APIResponse(status="success", message="Invitation cancelled")


@router.post("/teachers/invitations/{invitation_id}/resend")
@require_role("SCHOOL_ADMIN")
async def resend_teacher_invitation(
    invitation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Resend a teacher invitation with a new code."""
    invitation_service = get_teacher_invitation_service()

    try:
        invitation = await invitation_service.resend_invitation(db, invitation_id)
    except ValueError as e:
        return APIResponse(status="error", message=str(e))

    if not invitation:
        return APIResponse(status="error", message="Invitation not found")

    # Send email again
    tenant = await db.get(Tenant, get_tenant_id())
    tenant_name = tenant.name if tenant else "Your School"
    register_url = (
        f"{settings.app_base_url}/register/teacher?code={invitation.invitation_code}"
    )

    email_service = get_email_service()
    try:
        await email_service.send_teacher_invitation(
            to=invitation.email,
            tenant_name=tenant_name,
            teacher_name=invitation.first_name,
            invitation_code=invitation.invitation_code,
            register_url=register_url,
            expires_in_days=settings.invitation_code_expiry_days,
        )
    except Exception:
        logger.exception("Failed to resend teacher invitation email")

    return APIResponse(
        status="success",
        message=f"Invitation resent to {invitation.email}",
    )


@router.post("/teachers/{teacher_id}/set-password")
@require_role("SCHOOL_ADMIN")
async def admin_set_teacher_password(
    teacher_id: str,
    data: SetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """Admin directly sets a new password for a teacher."""
    user_service = get_user_service()
    try:
        teacher = await user_service.admin_set_password(
            db, uuid.UUID(teacher_id), data.password
        )
    except Exception as e:
        return APIResponse(
            status="error",
            message=str(e.message) if hasattr(e, "message") else "Failed to set password",
        )

    return APIResponse(
        status="success",
        message=f"Password updated for {teacher.first_name} {teacher.last_name}",
    )


@router.post("/teachers/{teacher_id}/reset-password")
@require_role("SCHOOL_ADMIN")
async def admin_send_reset_email(
    teacher_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Admin sends a password reset email to a teacher."""
    user_service = get_user_service()
    try:
        teacher = await user_service.get_user(db, uuid.UUID(teacher_id))
    except Exception as e:
        return APIResponse(
            status="error",
            message=str(e.message) if hasattr(e, "message") else "Teacher not found",
        )

    # Generate reset token and URL
    reset_token = create_password_reset_token(teacher.id)
    reset_url = f"{settings.app_base_url}/reset-password?token={reset_token}"

    email_service = get_email_service()
    try:
        await email_service.send_password_reset(
            to=teacher.email,
            user_name=teacher.first_name,
            reset_url=reset_url,
            expires_in_hours=24,
        )
    except Exception:
        logger.exception("Failed to send password reset email")
        return APIResponse(
            status="error",
            message="Failed to send reset email. Please try again.",
        )

    return APIResponse(
        status="success",
        message=f"Password reset email sent to {teacher.email}",
    )
