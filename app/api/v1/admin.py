"""Super Admin API routes for tenant management and platform settings."""

import logging
import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
import sqlalchemy as sa
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


class TenantFeaturesUpdateRequest(BaseModel):
    """Partial features update — merges into tenant.settings.features."""

    features: dict[str, bool] = Field(
        ...,
        description=(
            "Feature keys to update, mapped to True/False. Only the keys "
            "included are changed; other features are left as-is."
        ),
    )


@router.get("/tenants/{tenant_id}/features")
@require_super_admin()
async def get_tenant_features(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Read a tenant's features + the plan-level gating info super admin
    needs to render the toggles UI (which are locked vs. available vs. on).
    """
    from app.models import Tenant
    from app.services.subscription_service import get_subscription_service

    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        from app.exceptions import NotFoundException
        raise NotFoundException("Tenant not found")

    features = (tenant.settings or {}).get("features", {}) or {}

    plan_features: dict = {}
    try:
        sub = await get_subscription_service().get_tenant_subscription(db, tenant_id)
        if sub and sub.plan and sub.plan.features:
            plan_features = sub.plan.features
    except Exception:
        logger.exception("Failed to load subscription for tenant %s", tenant_id)

    return APIResponse(
        status="success",
        data={
            "tenant_id": str(tenant_id),
            "features": features,
            "plan_features": plan_features,
        },
    )


@router.put("/tenants/{tenant_id}/features")
@require_super_admin()
async def update_tenant_features(
    tenant_id: uuid.UUID,
    request: TenantFeaturesUpdateRequest,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Update a tenant's features (Super Admin only).

    Merges the given keys into tenant.settings.features — untouched keys are
    preserved, so this is safe to call with partial input from a UI panel
    that only exposes a subset of features (e.g. the WhatsApp toggles on
    the tenant edit page).

    Enforces the same opt-in gating as /settings/features: for opt-in
    features (WhatsApp / AI bot), the tenant's subscription plan must allow
    the feature before super admin can turn it on — otherwise the request
    is silently coerced to False. This keeps a single source of truth for
    what "the plan allows this" means, whether the toggle is flipped from
    the tenant admin UI or the super admin tenant editor.
    """
    from app.exceptions import NotFoundException
    from app.models import Tenant
    from app.services.subscription_service import get_subscription_service
    from app.services.whatsapp_bot import OPTIN_FEATURES

    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        raise NotFoundException("Tenant not found")

    plan_features: dict = {}
    try:
        sub = await get_subscription_service().get_tenant_subscription(db, tenant_id)
        if sub and sub.plan and sub.plan.features:
            plan_features = sub.plan.features
    except Exception:
        logger.exception("Failed to load subscription for tenant %s", tenant_id)

    settings = dict(tenant.settings or {})
    features = dict(settings.get("features", {}))

    for key, value in request.features.items():
        wanted = bool(value)
        if key in OPTIN_FEATURES:
            # Plan-gated opt-in — silently force off if plan disallows.
            features[key] = wanted if plan_features.get(key, False) else False
        else:
            features[key] = wanted

    settings["features"] = features
    tenant.settings = settings
    await db.commit()

    logger.info(
        "Super admin updated features for tenant %s: %s",
        tenant_id, request.features,
    )

    return APIResponse(
        status="success",
        data={"tenant_id": str(tenant_id), "features": features},
        message="Tenant features updated",
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


# ============================================================================
# WhatsApp — instance-level config (POC: prove the pipeline works, no bot yet)
# ============================================================================

class WhatsAppSettingsRequest(BaseModel):
    """Any subset of the 5 fields. MASKED preserves existing secrets."""
    phone_number_id: str | None = None
    business_account_id: str | None = None
    access_token: str | None = None
    verify_token: str | None = None
    app_secret: str | None = None


class SendWhatsAppTestRequest(BaseModel):
    to_phone: str = Field(..., min_length=8, max_length=32,
                          description="E.164 with or without leading +")
    template_name: str = Field("welcome", max_length=60,
                               description="Which pre-approved template to fire")
    school_name: str | None = Field(None, max_length=120,
                                    description="Filled into template params where relevant")


@router.get("/whatsapp-settings")
@require_super_admin()
async def get_whatsapp_settings(db: AsyncSession = Depends(get_db)) -> APIResponse:
    """Return the current WhatsApp config (secrets masked)."""
    from app.services import whatsapp_service

    cfg = await whatsapp_service.get_config(db)
    return APIResponse(status="success", data=cfg.with_masked_secrets())


@router.put("/whatsapp-settings")
@require_super_admin()
async def update_whatsapp_settings(
    body: WhatsAppSettingsRequest,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Save WhatsApp config. Fields sent as MASKED (********) preserve the
    existing stored value — matches the email + push + gateway pattern."""
    from app.services import whatsapp_service

    # Filter None so the admin can update a single field without wiping others
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    cfg = await whatsapp_service.save_config(db, updates)
    await db.commit()
    return APIResponse(
        status="success",
        message="WhatsApp settings saved.",
        data=cfg.with_masked_secrets(),
    )


@router.post("/whatsapp-settings/test")
@require_super_admin()
async def test_whatsapp_settings(db: AsyncSession = Depends(get_db)) -> APIResponse:
    """Verify Meta credentials by fetching the phone number info from Graph API.

    Doesn't send any actual message — cheapest possible way to know whether
    the access_token + phone_number_id combination is valid.
    """
    from app.services import whatsapp_service

    service = await whatsapp_service.get_whatsapp_service_from_db(db)
    ok, message = await service.test_connection()
    return APIResponse(
        status="success" if ok else "error",
        message=message,
    )


@router.post("/whatsapp-settings/send-test")
@require_super_admin()
async def send_whatsapp_test_message(
    body: SendWhatsAppTestRequest,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Fire an outbound WhatsApp template message to prove the send pipeline.

    Uses a pre-approved template because outside the 24hr session window
    Meta only lets templates through. The default `welcome` template exists
    on our approved list — any other template name will fail Meta's check
    if it hasn't been submitted + approved yet.
    """
    from app.services import whatsapp_service

    service = await whatsapp_service.get_whatsapp_service_from_db(db)
    if not service.is_configured:
        return APIResponse(
            status="error",
            message="WhatsApp is not fully configured yet. Fill in the credentials + test connection first.",
        )

    school = body.school_name or "ClassUp"
    if body.template_name == "welcome":
        params, button_urls = [school, "https://classup.co.za"], None
    elif body.template_name == "parent_signup":
        params, button_urls = [school], ["TESTCODE"]
    else:
        params, button_urls = None, None

    result = await service.send_template_message(
        to_phone=body.to_phone,
        template_name=body.template_name,
        language_code="en",
        parameters=params,
        button_url_variables=button_urls,
    )

    if not result:
        return APIResponse(
            status="error",
            message=(
                "Meta rejected the send. Check the server logs — the most "
                "common causes are: template not approved for this language, "
                "recipient number outside the WhatsApp allowlist during "
                "sandbox testing, or expired access token."
            ),
        )
    msg_id = (result.get("messages") or [{}])[0].get("id", "")
    return APIResponse(
        status="success",
        message=f"Template sent (Meta message id: {msg_id}). Check the recipient's WhatsApp.",
        data={"meta_message_id": msg_id},
    )


@router.get("/whatsapp-messages")
@require_super_admin()
async def list_recent_whatsapp_messages(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """The 'live view' — most recent inbound messages, newest first.

    Powers the admin page's auto-refreshing table so you can literally
    send a WhatsApp from your phone and watch it appear.
    """
    from app.models import Tenant, User, WhatsAppInboundMessage
    from sqlalchemy import select as sa_select

    result = await db.execute(
        sa_select(WhatsAppInboundMessage)
        .order_by(WhatsAppInboundMessage.created_at.desc())
        .limit(limit)
    )
    rows = list(result.scalars().all())

    # Batch-load user + tenant names for display
    user_ids = {r.matched_user_id for r in rows if r.matched_user_id}
    tenant_ids = {r.tenant_id for r in rows if r.tenant_id}
    users_map: dict = {}
    tenants_map: dict = {}
    if user_ids:
        users_res = await db.execute(
            sa_select(User).where(User.id.in_(user_ids))
        )
        users_map = {u.id: u for u in users_res.scalars().all()}
    if tenant_ids:
        tenants_res = await db.execute(
            sa_select(Tenant).where(Tenant.id.in_(tenant_ids))
        )
        tenants_map = {t.id: t for t in tenants_res.scalars().all()}

    return APIResponse(
        status="success",
        data=[
            {
                "id": str(r.id),
                "from_phone": r.from_phone,
                "message_type": r.message_type,
                "text": r.text,
                "matched_user": (
                    f"{users_map[r.matched_user_id].first_name} "
                    f"{users_map[r.matched_user_id].last_name}"
                ).strip()
                if r.matched_user_id in users_map else None,
                "tenant_name": tenants_map[r.tenant_id].name
                if r.tenant_id in tenants_map else None,
                "auto_replied": r.auto_replied,
                "auto_reply_error": r.auto_reply_error,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    )


# ==========================================================================
# WhatsApp conversations — full inbound + outbound thread per phone
#
# The ``/whatsapp-messages`` endpoint above is a one-sided firehose of
# inbound messages. This section adds the outbound side (from the new
# ``whatsapp_outbound_messages`` table) and joins the two into a
# proper chronological thread per parent, so super admin can review
# the full conversation — what a parent asked, what the bot said back,
# what notifications went out.
# ==========================================================================


@router.get("/whatsapp-conversations")
@require_super_admin()
async def list_whatsapp_conversations(
    q: str | None = Query(None, max_length=200),
    tenant_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Grouped-by-phone list of everyone who has messaged (or been
    messaged by) the school on WhatsApp.

    Each row: phone, matched parent (if any), tenant, last-message
    preview (with direction), last-message timestamp, message count.
    Newest activity first — the top of the list is who most recently
    talked to the bot or received a notification.
    """
    from app.models import Tenant, User, WhatsAppInboundMessage, WhatsAppOutboundMessage
    from sqlalchemy import select as sa_select, union_all, literal, func

    # Union of every message to/from every phone so we can rank by the
    # most recent activity across both sides.
    inb = sa_select(
        WhatsAppInboundMessage.from_phone.label("phone"),
        WhatsAppInboundMessage.text.label("body"),
        WhatsAppInboundMessage.message_type.label("msg_type"),
        WhatsAppInboundMessage.matched_user_id.label("user_id"),
        WhatsAppInboundMessage.tenant_id.label("tenant_id"),
        literal("IN").label("direction"),
        WhatsAppInboundMessage.created_at.label("created_at"),
    )
    outb = sa_select(
        WhatsAppOutboundMessage.to_phone.label("phone"),
        WhatsAppOutboundMessage.body_text.label("body"),
        WhatsAppOutboundMessage.message_type.label("msg_type"),
        WhatsAppOutboundMessage.target_user_id.label("user_id"),
        WhatsAppOutboundMessage.tenant_id.label("tenant_id"),
        literal("OUT").label("direction"),
        WhatsAppOutboundMessage.created_at.label("created_at"),
    )
    stream = union_all(inb, outb).subquery()

    # Aggregate per phone — latest message + total count.
    agg = sa_select(
        stream.c.phone,
        func.max(stream.c.created_at).label("last_at"),
        func.count().label("msg_count"),
    ).group_by(stream.c.phone).subquery()

    # Join back to grab the latest row per phone for the preview.
    latest = sa_select(stream).order_by(stream.c.created_at.desc()).subquery("latest")

    query = (
        sa_select(agg.c.phone, agg.c.last_at, agg.c.msg_count)
        .order_by(agg.c.last_at.desc())
    )

    if q:
        term = f"%{q.strip()}%"
        query = query.where(agg.c.phone.ilike(term))

    if tenant_id:
        # Filter to phones that have at least one message tagged with
        # this tenant on either side.
        query = query.where(
            agg.c.phone.in_(
                sa_select(stream.c.phone)
                .where(stream.c.tenant_id == tenant_id)
                .distinct()
            )
        )

    # Total count for pagination
    count_q = sa_select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    query = query.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(query)).all()

    # Enrich each row with the latest message preview + matched user + tenant.
    phones = [r.phone for r in rows]
    if not phones:
        return APIResponse(
            status="success",
            data={"conversations": [], "page": page, "page_size": page_size, "total": total},
        )

    # For each phone, grab the single most recent inbound OR outbound row.
    # SQLAlchemy doesn't do a lateral join here easily — one query per
    # phone is fine at page_size 25.
    conversations = []
    user_ids: set[uuid.UUID] = set()
    tenant_ids: set[uuid.UUID] = set()

    for row in rows:
        recent_stmt = (
            union_all(
                sa_select(
                    WhatsAppInboundMessage.text.label("body"),
                    WhatsAppInboundMessage.matched_user_id.label("user_id"),
                    WhatsAppInboundMessage.tenant_id.label("tenant_id"),
                    literal("IN").label("direction"),
                    WhatsAppInboundMessage.created_at.label("created_at"),
                ).where(WhatsAppInboundMessage.from_phone == row.phone),
                sa_select(
                    WhatsAppOutboundMessage.body_text.label("body"),
                    WhatsAppOutboundMessage.target_user_id.label("user_id"),
                    WhatsAppOutboundMessage.tenant_id.label("tenant_id"),
                    literal("OUT").label("direction"),
                    WhatsAppOutboundMessage.created_at.label("created_at"),
                ).where(WhatsAppOutboundMessage.to_phone == row.phone),
            )
            .order_by(sa.text("created_at DESC"))
            .limit(1)
        )
        rec = (await db.execute(recent_stmt)).first()
        preview_body = (rec.body if rec else "") or ""
        preview_dir = rec.direction if rec else "IN"
        user_id_for_row = rec.user_id if rec else None
        tenant_id_for_row = rec.tenant_id if rec else None
        if user_id_for_row:
            user_ids.add(user_id_for_row)
        if tenant_id_for_row:
            tenant_ids.add(tenant_id_for_row)
        conversations.append({
            "phone": row.phone,
            "last_at": row.last_at.isoformat(),
            "msg_count": int(row.msg_count),
            "preview": preview_body[:200],
            "preview_direction": preview_dir,
            "_user_id": user_id_for_row,
            "_tenant_id": tenant_id_for_row,
        })

    users_map: dict = {}
    tenants_map: dict = {}
    if user_ids:
        users_res = await db.execute(sa_select(User).where(User.id.in_(user_ids)))
        users_map = {u.id: u for u in users_res.scalars().all()}
    if tenant_ids:
        tenants_res = await db.execute(sa_select(Tenant).where(Tenant.id.in_(tenant_ids)))
        tenants_map = {t.id: t for t in tenants_res.scalars().all()}

    for c in conversations:
        u = users_map.get(c.pop("_user_id"))
        t = tenants_map.get(c.pop("_tenant_id"))
        c["matched_user"] = (
            {
                "id": str(u.id),
                "name": f"{u.first_name} {u.last_name}".strip(),
                "email": u.email,
            }
            if u else None
        )
        c["tenant"] = {"id": str(t.id), "name": t.name} if t else None

    return APIResponse(
        status="success",
        data={
            "conversations": conversations,
            "page": page,
            "page_size": page_size,
            "total": total,
        },
    )


@router.get("/whatsapp-conversations/{phone}")
@require_super_admin()
async def get_whatsapp_conversation(
    phone: str,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Full interleaved thread for one phone number — every inbound
    and every outbound, chronological, oldest first."""
    from app.models import Tenant, User, WhatsAppInboundMessage, WhatsAppOutboundMessage
    from sqlalchemy import select as sa_select

    # Trailing '+' is not part of the DB phone format (Meta strips it).
    normalized = phone.lstrip("+").strip()

    inb_rows = (await db.execute(
        sa_select(WhatsAppInboundMessage)
        .where(WhatsAppInboundMessage.from_phone == normalized)
        .order_by(WhatsAppInboundMessage.created_at.asc())
    )).scalars().all()

    outb_rows = (await db.execute(
        sa_select(WhatsAppOutboundMessage)
        .where(WhatsAppOutboundMessage.to_phone == normalized)
        .order_by(WhatsAppOutboundMessage.created_at.asc())
    )).scalars().all()

    # Merge + interleave by timestamp — bot replies always land after
    # their inbound trigger so ordering is deterministic even at same-
    # second creation.
    messages: list[dict] = []
    for r in inb_rows:
        messages.append({
            "id": str(r.id),
            "direction": "IN",
            "message_type": r.message_type,
            "text": r.text,
            "auto_replied": r.auto_replied,
            "auto_reply_error": r.auto_reply_error,
            "created_at": r.created_at.isoformat(),
        })
    for r in outb_rows:
        messages.append({
            "id": str(r.id),
            "direction": "OUT",
            "message_type": r.message_type,
            "template_name": r.template_name,
            "text": r.body_text,
            "meta_message_id": r.meta_message_id,
            "error": r.error,
            "inbound_message_id": str(r.inbound_message_id) if r.inbound_message_id else None,
            "created_at": r.created_at.isoformat(),
        })
    messages.sort(key=lambda m: m["created_at"])

    # Resolve matched user + tenant using the most recent inbound (or
    # outbound if there was no inbound) — same phone might get
    # multiple matched_user rows over time as users update their
    # numbers; latest wins.
    matched_user = None
    tenant = None
    all_rows = list(inb_rows) + list(outb_rows)
    if all_rows:
        latest = max(all_rows, key=lambda r: r.created_at)
        user_id = getattr(latest, "matched_user_id", None) or getattr(latest, "target_user_id", None)
        tenant_id_val = getattr(latest, "tenant_id", None)
        if user_id:
            u = (await db.execute(sa_select(User).where(User.id == user_id))).scalar_one_or_none()
            if u:
                matched_user = {
                    "id": str(u.id),
                    "name": f"{u.first_name} {u.last_name}".strip(),
                    "email": u.email,
                    "phone": u.phone,
                    "whatsapp_phone": u.whatsapp_phone,
                    "whatsapp_opted_in": u.whatsapp_opted_in,
                }
        if tenant_id_val:
            t = (await db.execute(sa_select(Tenant).where(Tenant.id == tenant_id_val))).scalar_one_or_none()
            if t:
                tenant = {"id": str(t.id), "name": t.name}

    return APIResponse(
        status="success",
        data={
            "phone": normalized,
            "matched_user": matched_user,
            "tenant": tenant,
            "messages": messages,
            "total": len(messages),
        },
    )


# ==========================================================================
# AI settings — Anthropic API key + model powering the AI-mode WhatsApp bot
# ==========================================================================


class AISettingsRequest(BaseModel):
    """PUT body for /admin/ai-settings. Every field optional so the admin
    can bump the daily cap without re-entering the API key."""

    api_key: str | None = Field(None, max_length=500)
    model: str | None = Field(None, max_length=100)
    daily_message_cap_per_user: int | None = Field(None, ge=1, le=10_000)
    max_conversation_turns: int | None = Field(None, ge=2, le=40)


@router.get("/ai-settings")
@require_super_admin()
async def get_ai_settings(db: AsyncSession = Depends(get_db)) -> APIResponse:
    """Return the current AI config (API key masked)."""
    from app.services import ai_config

    cfg = await ai_config.get_config(db)
    return APIResponse(status="success", data=cfg.with_masked_secrets())


@router.put("/ai-settings")
@require_super_admin()
async def update_ai_settings(
    body: AISettingsRequest,
    db: AsyncSession = Depends(get_db),
) -> APIResponse:
    """Save AI config. MASKED api_key preserves the existing key so the
    admin can edit the model / cap without re-entering the secret."""
    from app.services import ai_config

    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    cfg = await ai_config.save_config(db, updates)
    await db.commit()
    return APIResponse(
        status="success",
        message="AI settings saved.",
        data=cfg.with_masked_secrets(),
    )


@router.post("/ai-settings/test")
@require_super_admin()
async def test_ai_settings(db: AsyncSession = Depends(get_db)) -> APIResponse:
    """Verify the Anthropic API key + model with a minimal live call.

    Sends "Say OK." with an 8-token cap — under $0.0001, enough to
    confirm the key works and the model responds.
    """
    from app.services import ai_config

    cfg = await ai_config.get_config(db)
    ok, message = await ai_config.test_connection(cfg)
    return APIResponse(
        status="success" if ok else "error",
        message=message,
    )
