"""User API endpoints."""

import logging
import uuid

from fastapi import APIRouter, Depends, Query
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


class UpdateTeacherRequest(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None


class SetPasswordRequest(BaseModel):
    password: str = Field(..., min_length=8)


class UpdateParentRequest(BaseModel):
    """Admin edits a parent's profile on their behalf.

    Every field is optional — the endpoint applies only the ones that
    are present. ``phone`` and ``whatsapp_phone`` accept explicit ``""``
    to clear the field (mapped to NULL by the service).
    """
    first_name: str | None = None
    last_name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    whatsapp_phone: str | None = None
    whatsapp_opted_in: bool | None = None
    language: str | None = None


@router.get("/parents/search")
@require_role("SCHOOL_ADMIN")
async def search_parents(
    q: str | None = Query(
        None, min_length=1, max_length=100,
        description="Search term — matches name, email, or phone (case-insensitive)",
    ),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Search existing parents in this tenant.

    Powers the "invite parent" typeahead: as the admin types an email
    or name, we return up to ``limit`` matching parents WITH their
    linked children — so the admin can confirm the identity (*"yes,
    Jane Doe, parent of Sarah Moyo in Grade 3B — that's her"*) and
    link the new student with one click instead of running the full
    invite flow again. Also powers the "sibling of" picker on the
    student-create form.
    """
    from app.models.student import ParentStudent  # noqa: F401  (relationship load)

    service = get_user_service()
    parents = await service.get_parents_paginated(db, search=q, limit=limit)

    def _summary(p) -> dict:
        # Compact shape — the typeahead only needs enough to render
        # a "Jane Doe · jane@example.com · parent of Sarah, Peter" row.
        children = [
            {
                "id": str(ps.student.id),
                "first_name": ps.student.first_name,
                "last_name": ps.student.last_name,
                "is_primary": ps.is_primary,
            }
            for ps in (p.parent_students or [])
            if ps.student is not None and ps.student.deleted_at is None
        ]
        return {
            "id": str(p.id),
            "first_name": p.first_name,
            "last_name": p.last_name,
            "email": p.email,
            "phone": p.phone,
            "children": children,
        }

    return APIResponse(
        status="success",
        data={"parents": [_summary(p) for p in parents]},
    )


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


@router.put("/teachers/{teacher_id}")
@require_role("SCHOOL_ADMIN")
async def update_teacher(
    teacher_id: uuid.UUID,
    data: UpdateTeacherRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update a teacher's details."""
    user_service = get_user_service()
    try:
        teacher = await user_service.update_teacher(
            db,
            teacher_id,
            first_name=data.first_name,
            last_name=data.last_name,
            email=data.email,
            phone=data.phone if data.phone is not None else ...,
        )
    except Exception as e:
        return APIResponse(
            status="error",
            message=str(e.message) if hasattr(e, "message") else str(e),
        )

    return APIResponse(
        status="success",
        message=f"Teacher {teacher.first_name} {teacher.last_name} updated",
        data={
            "id": str(teacher.id),
            "first_name": teacher.first_name,
            "last_name": teacher.last_name,
            "email": teacher.email,
            "phone": teacher.phone,
        },
    )


@router.post("/teachers/{teacher_id}/deactivate")
@require_role("SCHOOL_ADMIN")
async def deactivate_teacher(
    teacher_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Deactivate a teacher account."""
    user_service = get_user_service()
    try:
        teacher = await user_service.deactivate_teacher(db, teacher_id)
    except Exception as e:
        return APIResponse(
            status="error",
            message=str(e.message) if hasattr(e, "message") else str(e),
        )

    return APIResponse(
        status="success",
        message=f"{teacher.first_name} {teacher.last_name} has been deactivated",
    )


@router.post("/teachers/{teacher_id}/activate")
@require_role("SCHOOL_ADMIN")
async def activate_teacher(
    teacher_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Activate a teacher account."""
    user_service = get_user_service()
    try:
        teacher = await user_service.activate_teacher(db, teacher_id)
    except Exception as e:
        return APIResponse(
            status="error",
            message=str(e.message) if hasattr(e, "message") else str(e),
        )

    return APIResponse(
        status="success",
        message=f"{teacher.first_name} {teacher.last_name} has been activated",
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


# =========================================================================
# Parent management (school admin managing parent accounts on their behalf)
#
# Mirrors the teacher endpoints above. Kept parallel rather than
# role-generic because the two flows have different concerns:
# parents have WhatsApp opt-in state and multi-child linkage that
# teachers don't. All endpoints below require SCHOOL_ADMIN.
# =========================================================================


def _parent_summary(parent, include_children: bool = True) -> dict:
    """Shape a parent User row for API responses (list + detail)."""
    data = {
        "id": str(parent.id),
        "first_name": parent.first_name,
        "last_name": parent.last_name,
        "email": parent.email,
        "phone": parent.phone,
        "whatsapp_phone": parent.whatsapp_phone,
        "whatsapp_opted_in": bool(parent.whatsapp_opted_in),
        "language": parent.language,
        "is_active": parent.is_active,
        "last_login_at": parent.last_login_at.isoformat() if parent.last_login_at else None,
        "created_at": parent.created_at.isoformat() if parent.created_at else None,
    }
    if include_children:
        data["children"] = [
            {
                "id": str(ps.student.id),
                "first_name": ps.student.first_name,
                "last_name": ps.student.last_name,
                "is_primary": ps.is_primary,
                "relationship_type": ps.relationship_type,
            }
            for ps in (parent.parent_students or [])
            if ps.student is not None and ps.student.deleted_at is None
        ]
    return data


@router.get("/parents")
@require_role("SCHOOL_ADMIN")
async def list_parents(
    q: str | None = Query(None, max_length=200),
    include_inactive: bool = Query(False),
    opted_in_only: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Paginated list of parents on the current tenant.

    Powers the Manage Parents admin page. Distinct from
    ``/parents/search`` (typeahead) — this one returns richer rows
    (WhatsApp opt-in state, active status, last login) and supports
    filter + pagination.
    """
    service = get_user_service()
    parents, total = await service.list_parents(
        db, search=q, include_inactive=include_inactive,
        opted_in_only=opted_in_only, page=page, page_size=page_size,
    )
    return APIResponse(
        status="success",
        data={
            "parents": [_parent_summary(p) for p in parents],
            "page": page,
            "page_size": page_size,
            "total": total,
        },
    )


@router.get("/parents/{parent_id}")
@require_role("SCHOOL_ADMIN")
async def get_parent(parent_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Return one parent with their linked children."""
    service = get_user_service()
    parent = await service.get_parent(db, parent_id)
    return APIResponse(status="success", data=_parent_summary(parent))


@router.patch("/parents/{parent_id}")
@require_role("SCHOOL_ADMIN")
async def update_parent(
    parent_id: uuid.UUID,
    data: UpdateParentRequest,
    db: AsyncSession = Depends(get_db),
):
    """Admin edits a parent's profile on their behalf.

    Empty-string values for ``phone`` / ``whatsapp_phone`` clear the
    field; omitted fields are left untouched. Toggling
    ``whatsapp_opted_in`` to True with no ``whatsapp_phone`` set
    copies the regular ``phone`` into ``whatsapp_phone`` so the
    opt-in flag actually enables sends.
    """
    service = get_user_service()
    try:
        # Sentinel `...` distinguishes "not sent by client" from
        # "sent as empty" — pydantic gives us None for both by default.
        payload = data.model_dump(exclude_unset=True)
        parent = await service.update_parent(
            db, parent_id,
            first_name=payload.get("first_name"),
            last_name=payload.get("last_name"),
            email=payload.get("email"),
            phone=payload["phone"] if "phone" in payload else ...,
            whatsapp_phone=payload["whatsapp_phone"] if "whatsapp_phone" in payload else ...,
            whatsapp_opted_in=payload.get("whatsapp_opted_in"),
            language=payload.get("language"),
        )
    except Exception as e:
        return APIResponse(
            status="error",
            message=getattr(e, "message", None) or str(e),
        )
    return APIResponse(
        status="success",
        message=f"{parent.first_name} {parent.last_name} updated",
        data=_parent_summary(parent),
    )


@router.post("/parents/{parent_id}/deactivate")
@require_role("SCHOOL_ADMIN")
async def deactivate_parent(
    parent_id: uuid.UUID, db: AsyncSession = Depends(get_db),
):
    service = get_user_service()
    try:
        parent = await service.deactivate_parent(db, parent_id)
    except Exception as e:
        return APIResponse(status="error", message=getattr(e, "message", None) or str(e))
    return APIResponse(
        status="success",
        message=f"{parent.first_name} {parent.last_name} has been deactivated",
    )


@router.post("/parents/{parent_id}/activate")
@require_role("SCHOOL_ADMIN")
async def activate_parent(
    parent_id: uuid.UUID, db: AsyncSession = Depends(get_db),
):
    service = get_user_service()
    try:
        parent = await service.activate_parent(db, parent_id)
    except Exception as e:
        return APIResponse(status="error", message=getattr(e, "message", None) or str(e))
    return APIResponse(
        status="success",
        message=f"{parent.first_name} {parent.last_name} has been reactivated",
    )


@router.post("/parents/{parent_id}/set-password")
@require_role("SCHOOL_ADMIN")
async def admin_set_parent_password(
    parent_id: uuid.UUID,
    data: SetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """Admin directly sets a new password for a parent (last-resort
    unlock — the reset-email flow is preferred and leaves no
    admin-known credential on the account)."""
    service = get_user_service()
    try:
        parent = await service.admin_set_parent_password(
            db, parent_id, data.password,
        )
    except Exception as e:
        return APIResponse(
            status="error",
            message=getattr(e, "message", None) or "Failed to set password",
        )
    return APIResponse(
        status="success",
        message=f"Password updated for {parent.first_name} {parent.last_name}",
    )


@router.post("/parents/{parent_id}/reset-password")
@require_role("SCHOOL_ADMIN")
async def admin_send_parent_reset_email(
    parent_id: uuid.UUID, db: AsyncSession = Depends(get_db),
):
    """Fire a standard password-reset email to the parent so they can
    pick a new password themselves. Preferred over ``/set-password``
    because the admin never learns the new credential."""
    service = get_user_service()
    try:
        parent = await service.get_parent(db, parent_id)
    except Exception as e:
        return APIResponse(
            status="error",
            message=getattr(e, "message", None) or "Parent not found",
        )

    reset_token = create_password_reset_token(parent.id)
    reset_url = f"{settings.app_base_url}/reset-password?token={reset_token}"

    email_service = get_email_service()
    try:
        await email_service.send_password_reset(
            to=parent.email,
            user_name=parent.first_name,
            reset_url=reset_url,
            expires_in_hours=24,
        )
    except Exception:
        logger.exception("Failed to send parent password reset email")
        return APIResponse(
            status="error",
            message="Failed to send reset email. Please try again.",
        )

    return APIResponse(
        status="success",
        message=f"Password reset email sent to {parent.email}",
    )
