"""Parent invitation API endpoints."""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
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
settings = get_settings()

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
        # Check if a parent with this email already exists in the tenant
        from app.models import ParentStudent, Student, Tenant, User
        from app.utils.tenant_context import get_tenant_id

        tenant_id = get_tenant_id()
        existing_parent_result = await db.execute(
            select(User).where(
                and_(
                    User.email == data.email.lower(),
                    User.tenant_id == tenant_id,
                    User.role == "PARENT",
                    User.deleted_at.is_(None),
                )
            )
        )
        existing_parent = existing_parent_result.scalar_one_or_none()

        if existing_parent:
            # Check if already linked to this student
            existing_link = await db.execute(
                select(ParentStudent).where(
                    and_(
                        ParentStudent.parent_id == existing_parent.id,
                        ParentStudent.student_id == data.student_id,
                    )
                )
            )
            if existing_link.scalar_one_or_none():
                raise HTTPException(
                    status_code=400,
                    detail="This parent is already linked to this student",
                )

            # Auto-link the existing parent to the student
            parent_student = ParentStudent(
                parent_id=existing_parent.id,
                student_id=data.student_id,
                relationship="PARENT",
                is_primary=False,
            )
            db.add(parent_student)
            await db.commit()

            # Send "child linked" notification email
            student = await db.get(Student, data.student_id)
            tenant = await db.get(Tenant, tenant_id)
            if student and tenant:
                try:
                    await email_service.send(
                        to=existing_parent.email,
                        subject=f"A new child has been linked to your {tenant.name} account",
                        template_name="parent_link_child.html",
                        context={
                            "parent_name": existing_parent.first_name,
                            "student_name": f"{student.first_name} {student.last_name}",
                            "tenant_name": tenant.name,
                            "login_url": f"{settings.app_base_url}/login",
                            "app_name": settings.app_name,
                        },
                    )
                except Exception as e:
                    logger.error(f"Failed to send child-linked email: {e}")

            return APIResponse(
                status="success",
                data=None,
                message=f"Parent {existing_parent.first_name} {existing_parent.last_name} has been linked to {student.first_name} {student.last_name} automatically (existing account)",
            )

        # No existing parent — create invitation as normal
        invitation = await service.create_invitation(
            db,
            student_id=data.student_id,
            email=data.email,
            first_name=data.first_name,
            last_name=data.last_name,
        )

        # Get student and tenant info for email
        student = await db.get(Student, invitation.student_id)
        tenant = await db.get(Tenant, invitation.tenant_id)

        # Send invitation email
        if student and tenant:
            from urllib.parse import urlencode
            params = urlencode({
                "code": invitation.invitation_code,
                "email": invitation.email,
            })
            register_url = f"{settings.app_base_url}/register?{params}"
            try:
                await email_service.send_parent_invitation(
                    to=invitation.email,
                    tenant_name=tenant.name,
                    student_name=f"{student.first_name} {student.last_name}",
                    invitation_code=invitation.invitation_code,
                    register_url=register_url,
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
            from urllib.parse import urlencode
            params = urlencode({
                "code": invitation.invitation_code,
                "email": invitation.email,
            })
            register_url = f"{settings.app_base_url}/register?{params}"
            try:
                await email_service.send_parent_invitation(
                    to=invitation.email,
                    tenant_name=tenant.name,
                    student_name=f"{student.first_name} {student.last_name}",
                    invitation_code=invitation.invitation_code,
                    register_url=register_url,
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
