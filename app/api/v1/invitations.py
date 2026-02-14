"""Parent invitation API endpoints."""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.common import APIResponse
from app.schemas.invitation import (
    InvitationCreate,
    InvitationListResponse,
    InvitationResponse,
    InvitationVerify,
    InvitationVerifyResponse,
)
from app.services.email_service import get_email_service
from app.services.invitation_service import get_invitation_service
from app.utils.permissions import require_role

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("", response_model=APIResponse)
@require_role("SCHOOL_ADMIN", "TEACHER")
async def list_invitations(
    status: str | None = None,
    student_id: UUID | None = None,
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """List parent invitations."""
    service = get_invitation_service()

    invitations, total = await service.list_invitations(
        db,
        status=status,
        student_id=student_id,
        page=page,
        page_size=page_size,
    )

    # Enrich with student/creator names
    from app.models import Student, User

    response_items = []
    for inv in invitations:
        student = await db.get(Student, inv.student_id)
        creator = await db.get(User, inv.created_by)

        response_items.append(
            InvitationResponse(
                id=inv.id,
                tenant_id=inv.tenant_id,
                student_id=inv.student_id,
                email=inv.email,
                invitation_code=inv.invitation_code,
                status=inv.status,
                created_by=inv.created_by,
                expires_at=inv.expires_at,
                accepted_at=inv.accepted_at,
                created_at=inv.created_at,
                student_name=f"{student.first_name} {student.last_name}" if student else None,
                created_by_name=f"{creator.first_name} {creator.last_name}" if creator else None,
            )
        )

    return APIResponse(
        status="success",
        data=InvitationListResponse(
            invitations=response_items,
            total=total,
            page=page,
            page_size=page_size,
        ),
    )


@router.post("", response_model=APIResponse)
@require_role("SCHOOL_ADMIN", "TEACHER")
async def create_invitation(
    data: InvitationCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new parent invitation."""
    service = get_invitation_service()
    email_service = get_email_service()

    try:
        invitation = await service.create_invitation(
            db,
            student_id=data.student_id,
            email=data.email,
        )

        # Get student and tenant info for email
        from app.models import Student, Tenant

        student = await db.get(Student, invitation.student_id)
        tenant = await db.get(Tenant, invitation.tenant_id)

        # Send invitation email
        if student and tenant:
            try:
                await email_service.send_parent_invitation(
                    to_email=invitation.email,
                    parent_name="Parent",
                    school_name=tenant.name,
                    student_name=f"{student.first_name} {student.last_name}",
                    invitation_code=invitation.invitation_code,
                    registration_url=f"/register?code={invitation.invitation_code}",
                )
            except Exception as e:
                logger.error(f"Failed to send invitation email: {e}")

        return APIResponse(
            status="success",
            data=InvitationResponse(
                id=invitation.id,
                tenant_id=invitation.tenant_id,
                student_id=invitation.student_id,
                email=invitation.email,
                invitation_code=invitation.invitation_code,
                status=invitation.status,
                created_by=invitation.created_by,
                expires_at=invitation.expires_at,
                created_at=invitation.created_at,
                student_name=f"{student.first_name} {student.last_name}" if student else None,
            ),
            message="Invitation created and email sent",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/verify", response_model=APIResponse)
async def verify_invitation(
    data: InvitationVerify,
    db: AsyncSession = Depends(get_db),
):
    """Verify an invitation code and email (public endpoint)."""
    service = get_invitation_service()

    result = await service.verify_invitation(db, code=data.code, email=data.email)

    return APIResponse(
        status="success",
        data=InvitationVerifyResponse(**result),
    )


@router.get("/{invitation_id}", response_model=APIResponse)
@require_role("SCHOOL_ADMIN", "TEACHER")
async def get_invitation(
    invitation_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get an invitation by ID."""
    service = get_invitation_service()

    invitation = await service.get_invitation(db, invitation_id)
    if not invitation:
        raise HTTPException(status_code=404, detail="Invitation not found")

    from app.models import Student, User

    student = await db.get(Student, invitation.student_id)
    creator = await db.get(User, invitation.created_by)

    return APIResponse(
        status="success",
        data=InvitationResponse(
            id=invitation.id,
            tenant_id=invitation.tenant_id,
            student_id=invitation.student_id,
            email=invitation.email,
            invitation_code=invitation.invitation_code,
            status=invitation.status,
            created_by=invitation.created_by,
            expires_at=invitation.expires_at,
            accepted_at=invitation.accepted_at,
            created_at=invitation.created_at,
            student_name=f"{student.first_name} {student.last_name}" if student else None,
            created_by_name=f"{creator.first_name} {creator.last_name}" if creator else None,
        ),
    )


@router.delete("/{invitation_id}", response_model=APIResponse)
@require_role("SCHOOL_ADMIN")
async def cancel_invitation(
    invitation_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Cancel an invitation."""
    service = get_invitation_service()

    success = await service.cancel_invitation(db, invitation_id)
    if not success:
        raise HTTPException(status_code=404, detail="Invitation not found")

    return APIResponse(
        status="success",
        message="Invitation cancelled",
    )


@router.post("/{invitation_id}/resend", response_model=APIResponse)
@require_role("SCHOOL_ADMIN", "TEACHER")
async def resend_invitation(
    invitation_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Resend an invitation email with a new code."""
    service = get_invitation_service()
    email_service = get_email_service()

    try:
        invitation = await service.resend_invitation(db, invitation_id)
        if not invitation:
            raise HTTPException(status_code=404, detail="Invitation not found")

        # Get student and tenant info for email
        from app.models import Student, Tenant

        student = await db.get(Student, invitation.student_id)
        tenant = await db.get(Tenant, invitation.tenant_id)

        # Send invitation email
        if student and tenant:
            try:
                await email_service.send_parent_invitation(
                    to_email=invitation.email,
                    parent_name="Parent",
                    school_name=tenant.name,
                    student_name=f"{student.first_name} {student.last_name}",
                    invitation_code=invitation.invitation_code,
                    registration_url=f"/register?code={invitation.invitation_code}",
                )
            except Exception as e:
                logger.error(f"Failed to send invitation email: {e}")

        return APIResponse(
            status="success",
            data=InvitationResponse(
                id=invitation.id,
                tenant_id=invitation.tenant_id,
                student_id=invitation.student_id,
                email=invitation.email,
                invitation_code=invitation.invitation_code,
                status=invitation.status,
                created_by=invitation.created_by,
                expires_at=invitation.expires_at,
                created_at=invitation.created_at,
                student_name=f"{student.first_name} {student.last_name}" if student else None,
            ),
            message="Invitation resent with new code",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
