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
    UpdateProfileRequest,
    UserProfile,
    VerifyInvitationRequest,
    VerifyInvitationResponse,
)
from app.schemas.common import APIResponse
from app.services.auth_service import get_auth_service
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
    # TODO: Implement email sending
    # For now, just return success message
    return APIResponse(
        data=ForgotPasswordResponse(),
        message="If your email is registered, you will receive password reset instructions",
    )


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
        )
    )
