"""Super Admin API routes for tenant management and platform settings."""

import logging
import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models.system_settings import SystemSettings
from app.models.tenant import EducationType
from app.schemas.common import APIResponse, PaginationMeta
from app.schemas.tenant import (
    PlatformStatsResponse,
    TenantAdminCreateRequest,
    TenantAdminResponse,
    TenantCreateRequest,
    TenantListItem,
    TenantResponse,
    TenantStatsResponse,
    TenantUpdateRequest,
)
from app.services.email_service import EMAIL_CONFIG_KEY, get_email_service
from app.services.tenant_service import get_tenant_service
from app.utils.permissions import require_super_admin
from app.utils.tenant_context import get_current_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.get("/tenants")
@require_super_admin()
async def list_tenants(
    is_active: bool | None = None,
    education_type: str | None = None,
    search: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """List all tenants with optional filters (Super Admin only)."""
    tenant_service = get_tenant_service()
    tenants, total = await tenant_service.get_tenants(
        db,
        is_active=is_active,
        education_type=education_type,
        search=search,
        page=page,
        page_size=page_size,
    )

    total_pages = (total + page_size - 1) // page_size

    return APIResponse(
        status="success",
        data=[TenantListItem.model_validate(t) for t in tenants],
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
            total_items=total,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_prev=page > 1,
        ),
    )


@router.post("/tenants")
@require_super_admin()
async def create_tenant(
    request: TenantCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Create a new tenant (Super Admin only)."""
    tenant_service = get_tenant_service()
    tenant = await tenant_service.create_tenant(
        db,
        name=request.name,
        email=request.email,
        education_type=request.education_type,
        phone=request.phone,
        address=request.address,
        slug=request.slug,
    )

    return APIResponse(
        status="success",
        data=TenantResponse.model_validate(tenant),
        message="Tenant created successfully",
    )


@router.get("/tenants/{tenant_id}")
@require_super_admin()
async def get_tenant(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Get a tenant by ID (Super Admin only)."""
    tenant_service = get_tenant_service()
    tenant = await tenant_service.get_tenant(db, tenant_id)

    return APIResponse(
        status="success",
        data=TenantResponse.model_validate(tenant),
    )


@router.put("/tenants/{tenant_id}")
@require_super_admin()
async def update_tenant(
    tenant_id: uuid.UUID,
    request: TenantUpdateRequest,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Update a tenant (Super Admin only)."""
    tenant_service = get_tenant_service()
    tenant = await tenant_service.update_tenant(
        db,
        tenant_id,
        name=request.name,
        slug=request.slug,
        email=request.email,
        phone=request.phone,
        address=request.address,
        is_active=request.is_active,
        settings=request.settings,
    )

    return APIResponse(
        status="success",
        data=TenantResponse.model_validate(tenant),
        message="Tenant updated successfully",
    )


@router.delete("/tenants/{tenant_id}")
@require_super_admin()
async def delete_tenant(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Soft delete a tenant (Super Admin only)."""
    tenant_service = get_tenant_service()
    await tenant_service.delete_tenant(db, tenant_id)

    return APIResponse(
        status="success",
        message="Tenant deleted successfully",
    )


@router.get("/tenants/{tenant_id}/stats")
@require_super_admin()
async def get_tenant_stats(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Get statistics for a specific tenant (Super Admin only)."""
    tenant_service = get_tenant_service()
    stats = await tenant_service.get_tenant_stats(db, tenant_id)

    return APIResponse(
        status="success",
        data=TenantStatsResponse(**stats),
    )


@router.get("/tenants/{tenant_id}/admins")
@require_super_admin()
async def get_tenant_admins(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Get all admin users for a tenant (Super Admin only)."""
    tenant_service = get_tenant_service()
    admins = await tenant_service.get_tenant_admins(db, tenant_id)

    return APIResponse(
        status="success",
        data=[TenantAdminResponse.model_validate(a) for a in admins],
    )


@router.post("/tenants/{tenant_id}/admins")
@require_super_admin()
async def create_tenant_admin(
    tenant_id: uuid.UUID,
    request: TenantAdminCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Create an admin user for a tenant (Super Admin only)."""
    tenant_service = get_tenant_service()
    admin = await tenant_service.create_tenant_admin(
        db,
        tenant_id=tenant_id,
        email=request.email,
        password=request.password,
        first_name=request.first_name,
        last_name=request.last_name,
        phone=request.phone,
    )

    return APIResponse(
        status="success",
        data=TenantAdminResponse.model_validate(admin),
        message="Admin user created successfully",
    )


@router.get("/stats")
@require_super_admin()
async def get_platform_stats(
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Get platform-wide statistics (Super Admin only)."""
    tenant_service = get_tenant_service()
    stats = await tenant_service.get_platform_stats(db)

    # Remove recent_tenants for API response (it contains ORM objects)
    api_stats = {k: v for k, v in stats.items() if k != "recent_tenants"}

    return APIResponse(
        status="success",
        data=PlatformStatsResponse(**api_stats),
    )


# --- Email Settings (SMTP / Resend) ---


class EmailConfigRequest(BaseModel):
    """Request schema for email configuration."""

    provider: str = Field("smtp", pattern="^(smtp|resend)$")
    enabled: bool = True
    from_email: str = Field(..., min_length=1, max_length=255)
    from_name: str = Field("ClassUp", max_length=255)
    # SMTP fields
    smtp_host: str = Field("", max_length=255)
    smtp_port: int = Field(587, ge=1, le=65535)
    smtp_username: str = Field("", max_length=255)
    smtp_password: str = Field("", max_length=255)
    smtp_use_tls: bool = True
    # Resend fields
    resend_api_key: str = Field("", max_length=255)


MASKED = "********"


@router.get("/email-settings")
@require_super_admin()
async def get_email_settings(
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Get current email settings (secrets masked)."""
    result = await db.execute(
        select(SystemSettings).where(SystemSettings.key == EMAIL_CONFIG_KEY)
    )
    row = result.scalar_one_or_none()

    if not row:
        return APIResponse(status="success", data=None)

    config = dict(row.value)
    if config.get("smtp_password"):
        config["smtp_password"] = MASKED
    if config.get("resend_api_key"):
        config["resend_api_key"] = MASKED

    return APIResponse(status="success", data=config)


@router.put("/email-settings")
@require_super_admin()
async def update_email_settings(
    request: EmailConfigRequest,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Save or update email settings."""
    result = await db.execute(
        select(SystemSettings).where(SystemSettings.key == EMAIL_CONFIG_KEY)
    )
    row = result.scalar_one_or_none()

    config = request.model_dump()

    # Preserve existing secrets if the masked placeholder was sent back
    if row:
        if config["smtp_password"] == MASKED:
            config["smtp_password"] = row.value.get("smtp_password", "")
        if config["resend_api_key"] == MASKED:
            config["resend_api_key"] = row.value.get("resend_api_key", "")

    if row:
        row.value = config
    else:
        row = SystemSettings(key=EMAIL_CONFIG_KEY, value=config)
        db.add(row)

    await db.flush()

    # Mask secrets in response
    resp = dict(config)
    if resp.get("smtp_password"):
        resp["smtp_password"] = MASKED
    if resp.get("resend_api_key"):
        resp["resend_api_key"] = MASKED

    return APIResponse(
        status="success",
        data=resp,
        message="Email settings saved successfully",
    )


class TestEmailRequest(BaseModel):
    """Optional recipient for the test email."""

    to: str | None = Field(None, max_length=255)


@router.post("/email-settings/test")
@require_super_admin()
async def test_email_settings(
    body: TestEmailRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Send a test email to a given address or the super admin's own email."""
    from app.models.user import User

    recipient = body.to.strip() if body and body.to and body.to.strip() else None

    if not recipient:
        user_id = get_current_user_id()
        user_result = await db.execute(select(User).where(User.id == user_id))
        user = user_result.scalar_one_or_none()
        if not user:
            return APIResponse(status="error", message="User not found")
        recipient = user.email
        recipient_name = user.first_name
    else:
        recipient_name = "Admin"

    email_service = get_email_service()
    result = await email_service.send(
        to=recipient,
        subject="ClassUp Test Email",
        template_name="welcome.html",
        context={
            "user_name": recipient_name,
            "tenant_name": "ClassUp Platform",
            "login_url": f"{get_settings().app_base_url}/login",
            "app_name": "ClassUp",
        },
    )

    if result:
        return APIResponse(
            status="success",
            message=f"Test email sent to {recipient}",
        )
    return APIResponse(
        status="error",
        message="Failed to send test email. Check settings and server logs.",
    )


# ============================================================================
# Web Push — platform-wide VAPID keypair management
# ============================================================================

class PushSettingsResponse(BaseModel):
    configured: bool
    public_key_b64url: str
    subject: str
    generated_at: str | None
    stats: dict


class GenerateKeysRequest(BaseModel):
    subject: str = Field(..., min_length=4, max_length=255, description="VAPID sub claim, e.g. mailto:admin@example.com")


class RotateKeysRequest(BaseModel):
    subject: str = Field(..., min_length=4, max_length=255)
    confirm: bool = Field(False, description="Must be true — rotating invalidates all existing subscriptions")


class UpdateSubjectRequest(BaseModel):
    subject: str = Field(..., min_length=4, max_length=255)


@router.get("/push-settings")
@require_super_admin()
async def get_push_settings(db: AsyncSession = Depends(get_db)) -> APIResponse:
    """Current VAPID config + platform-wide subscription stats."""
    from app.services import push_service

    cfg = await push_service.get_vapid_config(db)
    stats = await push_service.count_subscriptions(db)

    # Look up generated_at separately because get_vapid_config doesn't return it
    generated_at = None
    row = await db.execute(
        select(SystemSettings).where(SystemSettings.key == push_service.VAPID_SETTINGS_KEY)
    )
    s = row.scalar_one_or_none()
    if s and s.value:
        generated_at = s.value.get("generated_at")

    return APIResponse(
        status="success",
        data=PushSettingsResponse(
            configured=cfg.configured,
            public_key_b64url=cfg.public_key_b64url,
            subject=cfg.subject,
            generated_at=generated_at,
            stats=stats,
        ).model_dump(),
    )


@router.post("/push-settings/generate")
@require_super_admin()
async def generate_push_keys(
    body: GenerateKeysRequest,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """First-time keypair creation. Fails (409) if a keypair already exists —
    use /rotate to overwrite."""
    from app.services import push_service

    try:
        result = await push_service.generate_and_store_keypair(
            db, subject=body.subject.strip(), force=False
        )
    except ValueError as e:
        return APIResponse(status="error", message=str(e))
    await db.commit()
    return APIResponse(
        status="success",
        message="VAPID keypair created. Users can now enable push notifications.",
        data=result,
    )


@router.post("/push-settings/rotate")
@require_super_admin()
async def rotate_push_keys(
    body: RotateKeysRequest,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Destructive: overwrites the existing keypair. Every active push
    subscription becomes useless; users must re-enable on each device."""
    from app.services import push_service

    if not body.confirm:
        return APIResponse(
            status="error",
            message="confirm=true is required to rotate — this invalidates every existing subscription.",
        )
    try:
        result = await push_service.generate_and_store_keypair(
            db, subject=body.subject.strip(), force=True
        )
    except ValueError as e:
        return APIResponse(status="error", message=str(e))
    await db.commit()
    return APIResponse(
        status="success",
        message="VAPID keypair rotated. All existing subscriptions are now invalid.",
        data=result,
    )


@router.put("/push-settings/subject")
@require_super_admin()
async def update_push_subject(
    body: UpdateSubjectRequest,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Change just the VAPID subject (contact mailto:) without rotating keys."""
    from app.services import push_service

    try:
        new_subject = await push_service.update_subject(db, body.subject.strip())
    except ValueError as e:
        return APIResponse(status="error", message=str(e))
    await db.commit()
    return APIResponse(
        status="success",
        message="Subject updated.",
        data={"subject": new_subject},
    )


class SendTestPushRequest(BaseModel):
    """Super admin sends a test push to a specific user (by email).

    Useful for verifying the platform-wide push pipeline without first
    having to enable push on the admin's own browser. Also useful when a
    user reports "I'm not getting notifications" — admin can fire a test
    and observe whether their devices receive it.
    """
    email: str = Field(..., min_length=3, max_length=255)
    title: str | None = Field(None, max_length=120)
    body: str | None = Field(None, max_length=500)


@router.post("/push-settings/send-test")
@require_super_admin()
async def send_test_push(
    body: SendTestPushRequest,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Send a hello-world push to every device belonging to the named user.

    Useful for verifying:
      1. VAPID is configured (returns 503 if not)
      2. The target user has any subscriptions at all
      3. End-to-end delivery to FCM/APNs/Mozilla
      4. The user's specific device is actually receiving pushes
    """
    from app.models.user import User
    from app.services import push_service

    cfg = await push_service.get_vapid_config(db)
    if not cfg.configured:
        return APIResponse(
            status="error",
            message="VAPID is not configured. Generate a keypair before sending test pushes.",
        )

    # Find the user (case-insensitive; users may be in any tenant)
    email = body.email.strip().lower()
    result = await db.execute(
        select(User).where(User.email == email, User.is_active.is_(True))
    )
    user = result.scalars().first()
    if not user:
        return APIResponse(
            status="error",
            message=f"No active user found with email '{email}'.",
        )

    payload = {
        "title": body.title or "ClassUp admin test",
        "body": body.body or "This is a test push from the platform admin. If you can see this, push is working on this device.",
        "url": "/dashboard",
        "tag": "classup-admin-test",
    }
    send_result = await push_service.send_to_user(db, user.id, payload)
    await db.commit()

    if send_result.sent == 0 and send_result.deleted_dead == 0:
        return APIResponse(
            status="error",
            message=(
                f"User {email} has no active push subscriptions yet. "
                f"Ask them to open Settings → Notifications and tap Enable."
            ),
        )

    return APIResponse(
        status="success",
        message=(
            f"Sent to {send_result.sent} device(s). "
            f"{send_result.deleted_dead} dead subscription(s) cleaned up. "
            f"{send_result.failed} send(s) failed (see server logs)."
        ),
        data={
            "user_email": email,
            "user_id": str(user.id),
            "sent": send_result.sent,
            "deleted_dead": send_result.deleted_dead,
            "failed": send_result.failed,
        },
    )


# ============================================================================
# Platform defaults — super-admin settings new tenants inherit on signup
# ============================================================================

class PlatformDefaultsRequest(BaseModel):
    platform_name: str = Field(..., min_length=1, max_length=120)
    support_email: str = Field("", max_length=255)
    support_phone: str = Field("", max_length=40)
    default_currency: str = Field(..., min_length=3, max_length=3)
    default_country: str = Field(..., min_length=2, max_length=2)
    default_language: str = Field(..., min_length=2, max_length=10)
    default_timezone: str = Field(..., min_length=1, max_length=64)


@router.get("/platform-settings")
@require_super_admin()
async def get_platform_settings(db: AsyncSession = Depends(get_db)) -> APIResponse:
    """Current platform defaults + reference data for the UI dropdowns."""
    from app.services import platform_service

    defaults = await platform_service.get_defaults(db)
    return APIResponse(
        status="success",
        data={
            "settings": defaults.to_dict(),
            "currencies": platform_service.SUPPORTED_CURRENCIES,
            "countries": platform_service.SUPPORTED_COUNTRIES,
            "languages": platform_service.SUPPORTED_LANGUAGES,
            "timezones": platform_service.COMMON_TIMEZONES,
        },
    )


@router.put("/platform-settings")
@require_super_admin()
async def update_platform_settings(
    body: PlatformDefaultsRequest,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Write platform defaults. Affects:

      - New tenants created from this point on (inherit these values)
      - Pages that read platform-level fallbacks (emails sent before tenant
        assignment, super-admin reports showing platform-wide totals, etc.)

    Existing tenants are unaffected — they keep whatever was set in their own
    tenant.settings JSONB.
    """
    from app.services import platform_service

    updated = await platform_service.update_defaults(db, body.model_dump())
    await db.commit()
    return APIResponse(
        status="success",
        message="Platform settings saved. New tenants will inherit these defaults.",
        data={"settings": updated.to_dict()},
    )


# ============================================================================
# Payment gateways — instance-level singleton (one provider per ClassUp
# deployment; admin picks the country's gateway: Yoco for SA, Paynow for Zim,
# etc. Manual EFT-with-POP remains as the universal fallback)
# ============================================================================

class GatewayConfigRequest(BaseModel):
    provider_id: str = Field(..., description="Provider slug, e.g. 'yoco' or 'paynow'. Empty string clears the config.")
    is_enabled: bool = Field(True)
    credentials: dict = Field(default_factory=dict)


class GatewayTestRequest(BaseModel):
    provider_id: str = Field(..., min_length=1)
    credentials: dict = Field(default_factory=dict)


@router.get("/payment-gateways")
@require_super_admin()
async def get_payment_gateway(db: AsyncSession = Depends(get_db)) -> APIResponse:
    """Return the current gateway config (secrets masked) + the catalogue of
    providers the UI can offer."""
    from app.services import gateway_service

    cfg = await gateway_service.get_config(db)
    return APIResponse(
        status="success",
        data={
            "current": cfg.with_masked_secrets(),
            "providers": gateway_service.list_providers(),
        },
    )


@router.put("/payment-gateways")
@require_super_admin()
async def update_payment_gateway(
    body: GatewayConfigRequest,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Save the chosen provider + credentials. MASKED placeholders preserve
    the existing secret (so the admin doesn't have to re-enter the key just
    to toggle is_enabled)."""
    from app.services import gateway_service

    # Allow empty provider_id to clear the config — turns off gateway,
    # falling back to EFT-with-POP only
    if body.provider_id and body.provider_id not in gateway_service.PROVIDER_REGISTRY:
        return APIResponse(
            status="error",
            message=f"Unknown provider: {body.provider_id}",
        )

    try:
        updated = await gateway_service.save_config(
            db,
            provider_id=body.provider_id or "",
            is_enabled=body.is_enabled,
            credentials=body.credentials or {},
        )
    except ValueError as e:
        return APIResponse(status="error", message=str(e))
    await db.commit()
    return APIResponse(
        status="success",
        message=(
            "Payment gateway disabled. Tenants will see EFT-only on /subscription."
            if not body.provider_id else
            f"Saved. Tenants can now pay subscriptions via {body.provider_id}."
        ),
        data=updated.with_masked_secrets(),
    )


@router.post("/payment-gateways/test")
@require_super_admin()
async def test_payment_gateway(
    body: GatewayTestRequest,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Send a low-impact request to verify the credentials work BEFORE the
    admin saves them. MASKED secrets are hydrated from the stored config."""
    from app.services import gateway_service

    ok, message = await gateway_service.test_config(
        db, provider_id=body.provider_id, credentials=body.credentials or {}
    )
    return APIResponse(
        status="success" if ok else "error",
        message=message,
    )
