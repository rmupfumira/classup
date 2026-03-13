"""Authentication API endpoints."""

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.exceptions import UnauthorizedException
from app.schemas.auth import (
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    LoginResponse,
    RefreshTokenRequest,
    RegisterRequest,
    RegisterResponse,
    ResetPasswordRequest,
    TrialSignupRequest,
    UpdateProfileRequest,
    UserProfile,
    VerifyInvitationRequest,
    VerifyInvitationResponse,
)
from app.schemas.common import APIResponse
from app.services.auth_service import get_auth_service
from app.services.email_service import get_email_service
from app.services.user_service import get_user_service
from app.utils.security import create_password_reset_token, decode_password_reset_token, hash_password
from app.utils.tenant_context import get_current_user_id_or_none

router = APIRouter()


@router.post("/login", response_model=APIResponse[LoginResponse])
async def login(
    request: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Authenticate user and return tokens.

    Sets an HttpOnly cookie for web clients in addition to returning
    tokens in the response body for API clients.
    """
    auth_service = get_auth_service()
    login_response, user = await auth_service.login(db, request)

    # Set HttpOnly cookie for web clients
    cookie_max_age = settings.jwt_access_token_expire_minutes * 60
    if request.remember_me:
        cookie_max_age = settings.jwt_refresh_token_expire_days * 24 * 60 * 60

    response.set_cookie(
        key="access_token",
        value=login_response.access_token,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        max_age=cookie_max_age,
    )

    return APIResponse(
        data=login_response,
        message=f"Welcome back, {user.first_name}!",
    )


@router.post("/register", response_model=APIResponse[RegisterResponse])
async def register(
    request: RegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    """Register a new parent user via invitation code."""
    auth_service = get_auth_service()
    result = await auth_service.register_parent(db, request)

    return APIResponse(
        data=result,
        message="Registration successful! You can now log in.",
    )


@router.post("/refresh", response_model=APIResponse[LoginResponse])
async def refresh_token(
    request: RefreshTokenRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Refresh access token using refresh token."""
    auth_service = get_auth_service()
    login_response = await auth_service.refresh_token(db, request.refresh_token)

    # Update cookie
    response.set_cookie(
        key="access_token",
        value=login_response.access_token,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        max_age=settings.jwt_access_token_expire_minutes * 60,
    )

    return APIResponse(data=login_response)


@router.post("/logout", response_model=APIResponse[None])
async def logout(response: Response):
    """Clear authentication cookie.

    Note: This only clears the cookie. The JWT tokens remain valid until
    they expire. For true token revocation, implement a token blacklist.
    """
    response.delete_cookie(
        key="access_token",
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
    )

    return APIResponse(message="Logged out successfully")


@router.post("/forgot-password", response_model=APIResponse[ForgotPasswordResponse])
async def forgot_password(
    request: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """Request a password reset email.

    Always returns success to prevent email enumeration attacks.
    """
    import logging
    from sqlalchemy import select
    from app.models import User

    logger = logging.getLogger(__name__)

    # Look up user by email (don't reveal if not found)
    stmt = select(User).where(
        User.email == request.email,
        User.deleted_at.is_(None),
        User.is_active == True,
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if user:
        reset_token = create_password_reset_token(user.id)
        reset_url = f"{settings.app_base_url}/reset-password?token={reset_token}"

        email_service = get_email_service()
        try:
            await email_service.send_password_reset(
                to=user.email,
                user_name=user.first_name,
                reset_url=reset_url,
                expires_in_hours=24,
            )
        except Exception:
            logger.exception("Failed to send password reset email")

    return APIResponse(
        data=ForgotPasswordResponse(),
        message="If your email is registered, you will receive password reset instructions",
    )


@router.post("/reset-password", response_model=APIResponse[None])
async def reset_password(
    request: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """Reset password using a valid reset token."""
    import uuid as uuid_mod
    from sqlalchemy import select
    from app.models import User

    payload = decode_password_reset_token(request.token)
    if not payload:
        raise UnauthorizedException("Invalid or expired reset token")

    user_id = uuid_mod.UUID(payload["sub"])

    stmt = select(User).where(User.id == user_id, User.deleted_at.is_(None))
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise UnauthorizedException("Invalid or expired reset token")

    user.password_hash = hash_password(request.password)
    await db.commit()

    return APIResponse(message="Password reset successfully. You can now log in.")


@router.get("/me", response_model=APIResponse[UserProfile])
async def get_current_user(db: AsyncSession = Depends(get_db)):
    """Get the current authenticated user's profile."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        raise UnauthorizedException("Not authenticated")

    auth_service = get_auth_service()
    user = await auth_service.get_current_user(db, user_id)

    return APIResponse(data=UserProfile.model_validate(user))


@router.put("/me", response_model=APIResponse[UserProfile])
async def update_profile(
    request: UpdateProfileRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update the current user's profile."""
    user_id = get_current_user_id_or_none()
    if not user_id:
        raise UnauthorizedException("Not authenticated")

    auth_service = get_auth_service()
    user = await auth_service.get_current_user(db, user_id)

    # Update fields
    update_data = request.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(user, field, value)

    await db.commit()
    await db.refresh(user)

    return APIResponse(
        data=UserProfile.model_validate(user),
        message="Profile updated successfully",
    )


@router.post("/invitations/verify", response_model=APIResponse[VerifyInvitationResponse])
async def verify_invitation(
    request: VerifyInvitationRequest,
    db: AsyncSession = Depends(get_db),
):
    """Verify an invitation code and email combination.

    This endpoint is public (no auth required) and is used during
    the parent registration flow.
    """
    auth_service = get_auth_service()
    is_valid, invitation = await auth_service.verify_invitation(
        db, request.code, request.email
    )

    if not is_valid or not invitation:
        return APIResponse(
            data=VerifyInvitationResponse(
                valid=False,
                message="Invalid invitation code or email",
            )
        )

    # Get student and school names
    student_name = None
    school_name = None

    if invitation.student:
        student_name = invitation.student.full_name
    if invitation.tenant:
        school_name = invitation.tenant.name

    return APIResponse(
        data=VerifyInvitationResponse(
            valid=True,
            student_name=student_name,
            school_name=school_name,
            first_name=invitation.first_name if invitation.first_name else None,
            last_name=invitation.last_name if invitation.last_name else None,
        )
    )


@router.post("/trial-signup", response_model=APIResponse[None])
async def trial_signup(
    request: TrialSignupRequest,
    db: AsyncSession = Depends(get_db),
):
    """Handle free trial signup from the marketing site.

    This is a public endpoint (no auth required). It sends a notification
    email to the SUPER_ADMIN with the school details, and a confirmation
    email to the applicant.
    """
    import logging
    from sqlalchemy import select
    from app.models import User
    from app.models.user import Role

    logger = logging.getLogger(__name__)

    school_type_labels = {
        "daycare": "Daycare / Creche",
        "primary_school": "Primary School",
        "high_school": "High School",
        "combined": "Combined",
        "other": "Other",
    }
    school_type_label = school_type_labels.get(request.school_type, request.school_type)
    if request.school_type == "other" and request.school_type_other:
        school_type_label = f"Other — {request.school_type_other}"

    location_parts = [request.country or "South Africa"]
    if request.province:
        location_parts.append(request.province)
    location_label = ", ".join(location_parts)

    # Look up SUPER_ADMIN email
    stmt = select(User).where(
        User.role == Role.SUPER_ADMIN.value,
        User.is_active == True,
        User.deleted_at.is_(None),
    )
    result = await db.execute(stmt)
    super_admin = result.scalar_one_or_none()

    email_service = get_email_service()

    # Build admin notification email
    admin_html = f"""
    <div style="font-family: 'Inter', Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: linear-gradient(135deg, #1B3A6B 0%, #0f2544 100%); padding: 32px; border-radius: 12px 12px 0 0;">
            <h1 style="color: #C9962A; margin: 0; font-size: 24px;">New Trial Signup Request</h1>
        </div>
        <div style="background: #ffffff; padding: 32px; border: 1px solid #e5e7eb; border-top: none; border-radius: 0 0 12px 12px;">
            <table style="width: 100%; border-collapse: collapse;">
                <tr style="border-bottom: 1px solid #f3f4f6;">
                    <td style="padding: 12px 0; color: #6b7280; font-size: 14px; width: 140px;">School Name</td>
                    <td style="padding: 12px 0; color: #111827; font-size: 14px; font-weight: 600;">{request.school_name}</td>
                </tr>
                <tr style="border-bottom: 1px solid #f3f4f6;">
                    <td style="padding: 12px 0; color: #6b7280; font-size: 14px;">Contact Person</td>
                    <td style="padding: 12px 0; color: #111827; font-size: 14px; font-weight: 600;">{request.contact_name}</td>
                </tr>
                <tr style="border-bottom: 1px solid #f3f4f6;">
                    <td style="padding: 12px 0; color: #6b7280; font-size: 14px;">Email</td>
                    <td style="padding: 12px 0; color: #111827; font-size: 14px;"><a href="mailto:{request.email}" style="color: #1B3A6B;">{request.email}</a></td>
                </tr>
                <tr style="border-bottom: 1px solid #f3f4f6;">
                    <td style="padding: 12px 0; color: #6b7280; font-size: 14px;">Phone</td>
                    <td style="padding: 12px 0; color: #111827; font-size: 14px;">{request.phone}</td>
                </tr>
                <tr style="border-bottom: 1px solid #f3f4f6;">
                    <td style="padding: 12px 0; color: #6b7280; font-size: 14px;">Location</td>
                    <td style="padding: 12px 0; color: #111827; font-size: 14px;">{location_label}</td>
                </tr>
                <tr style="border-bottom: 1px solid #f3f4f6;">
                    <td style="padding: 12px 0; color: #6b7280; font-size: 14px;">School Type</td>
                    <td style="padding: 12px 0; color: #111827; font-size: 14px;">{school_type_label}</td>
                </tr>
                <tr style="border-bottom: 1px solid #f3f4f6;">
                    <td style="padding: 12px 0; color: #6b7280; font-size: 14px;">Students</td>
                    <td style="padding: 12px 0; color: #111827; font-size: 14px;">{request.student_count or 'Not specified'}</td>
                </tr>
                {"<tr><td style='padding: 12px 0; color: #6b7280; font-size: 14px; vertical-align: top;'>Message</td><td style='padding: 12px 0; color: #111827; font-size: 14px;'>" + request.message + "</td></tr>" if request.message else ""}
            </table>
        </div>
    </div>
    """

    # Send notification to super admin
    if super_admin:
        try:
            await email_service.send_raw_email(
                to=super_admin.email,
                subject=f"New Trial Signup: {request.school_name}",
                html_body=admin_html,
                from_name="ClassUp Signups",
            )
        except Exception:
            logger.exception("Failed to send trial signup notification to admin")

    # Send confirmation to applicant
    confirmation_html = f"""
    <div style="font-family: 'Inter', Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: linear-gradient(135deg, #1B3A6B 0%, #0f2544 100%); padding: 32px; border-radius: 12px 12px 0 0; text-align: center;">
            <h1 style="color: #C9962A; margin: 0; font-size: 24px;">Welcome to ClassUp!</h1>
        </div>
        <div style="background: #ffffff; padding: 32px; border: 1px solid #e5e7eb; border-top: none; border-radius: 0 0 12px 12px;">
            <p style="color: #374151; font-size: 16px; line-height: 1.6; margin: 0 0 16px;">
                Hi {request.contact_name},
            </p>
            <p style="color: #374151; font-size: 16px; line-height: 1.6; margin: 0 0 16px;">
                Thank you for your interest in ClassUp! We've received your free trial request for <strong>{request.school_name}</strong>.
            </p>
            <p style="color: #374151; font-size: 16px; line-height: 1.6; margin: 0 0 16px;">
                Our team will review your details and get in touch within <strong>24 hours</strong> to set up your account and walk you through the platform.
            </p>
            <p style="color: #374151; font-size: 16px; line-height: 1.6; margin: 0 0 24px;">
                In the meantime, if you have any questions, feel free to reply to this email.
            </p>
            <div style="background: #f9fafb; border-radius: 8px; padding: 20px; margin-bottom: 24px;">
                <p style="color: #6b7280; font-size: 13px; margin: 0 0 8px;">What happens next:</p>
                <ol style="color: #374151; font-size: 14px; line-height: 1.8; margin: 0; padding-left: 20px;">
                    <li>We'll create your ClassUp account</li>
                    <li>You'll receive login credentials via email</li>
                    <li>Enjoy 1 month of full access — no credit card required</li>
                </ol>
            </div>
            <p style="color: #6b7280; font-size: 14px; line-height: 1.6; margin: 0;">
                Best regards,<br>
                <strong style="color: #1B3A6B;">The ClassUp Team</strong>
            </p>
        </div>
    </div>
    """

    try:
        await email_service.send_raw_email(
            to=request.email,
            subject="Welcome to ClassUp — Your Free Trial Request",
            html_body=confirmation_html,
            from_name="ClassUp",
        )
    except Exception:
        logger.exception(f"Failed to send trial confirmation to {request.email}")

    return APIResponse(message="Thank you! We'll be in touch within 24 hours to set up your free trial.")
