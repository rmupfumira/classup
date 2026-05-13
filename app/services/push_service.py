"""Web Push service — sends notification payloads to subscribed devices.

Stack:
  - pywebpush handles the encryption + HTTP POST to FCM/APNs/Mozilla
  - py_vapid signs the JWT used for VAPID authentication
  - cryptography loads the private key from SEC1 PEM

Key gotchas (lessons learned, do NOT undo):

  1. PEM format MUST be SEC1 (TraditionalOpenSSL, "-----BEGIN EC PRIVATE
     KEY-----"). PKCS8 ("-----BEGIN PRIVATE KEY-----") makes py_vapid's
     from_pem() blow up with "Could not deserialize key data (e.g. EC
     curves with explicit parameters)".

  2. Do NOT pass the PEM string directly to webpush(vapid_private_key=...).
     pywebpush 2.x treats a string as base64url-encoded raw bytes (calls
     Vapid02.from_raw), which fails on PEM. Instead, load the key with
     cryptography, attach it to a Vapid02 instance manually, and pass the
     instance.

  3. On a 410 Gone or 404 Not Found from the push service, the subscription
     is dead — delete it. Anything else, just record the error and try
     again later.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import base64

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from py_vapid import Vapid02
from pywebpush import WebPushException, webpush
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PushSubscription, SystemSettings

logger = logging.getLogger(__name__)

VAPID_SETTINGS_KEY = "vapid_config"
DEFAULT_TTL = 24 * 60 * 60  # seconds; push service holds it for up to a day


@dataclass(frozen=True)
class VapidConfig:
    public_key_b64url: str
    private_pem: str
    subject: str

    @property
    def configured(self) -> bool:
        return bool(self.public_key_b64url) and bool(self.private_pem) and bool(self.subject)


# Cached at module level so we don't deserialize the key on every send. We
# refresh it on cache miss (first send), and external "key rotated" admin
# actions should restart the worker process — that's the simplest invariant.
_vapid_instance: Vapid02 | None = None
_vapid_config: VapidConfig | None = None


def _build_vapid_instance(private_pem: str) -> Vapid02:
    """Load a SEC1 PEM private key into a Vapid02 instance pywebpush accepts.

    We do this manually because pywebpush.webpush() mishandles PEM strings.
    """
    loaded = load_pem_private_key(
        private_pem.encode("ascii"), password=None, backend=default_backend()
    )
    v = Vapid02()
    v._private_key = loaded
    v._public_key = loaded.public_key()
    return v


async def get_vapid_config(db: AsyncSession) -> VapidConfig:
    """Read the VAPID keypair from system_settings.

    Returns a config with empty strings if not yet generated — callers
    check `configured` and surface "server not configured" in the UI.
    """
    result = await db.execute(
        select(SystemSettings).where(SystemSettings.key == VAPID_SETTINGS_KEY)
    )
    row = result.scalar_one_or_none()
    if not row or not row.value:
        return VapidConfig(public_key_b64url="", private_pem="", subject="")

    value = row.value or {}
    return VapidConfig(
        public_key_b64url=str(value.get("public_key_b64url", "")),
        private_pem=str(value.get("private_pem", "")),
        subject=str(value.get("subject", "mailto:admin@classup.co.za")),
    )


async def _get_vapid_instance(db: AsyncSession) -> tuple[Vapid02, VapidConfig] | None:
    """Cached accessor — returns (instance, config) or None if unconfigured."""
    global _vapid_instance, _vapid_config

    if _vapid_instance is not None and _vapid_config is not None:
        return _vapid_instance, _vapid_config

    config = await get_vapid_config(db)
    if not config.configured:
        return None
    _vapid_instance = _build_vapid_instance(config.private_pem)
    _vapid_config = config
    return _vapid_instance, _vapid_config


def reset_cache() -> None:
    """Clear the module-level VAPID cache. Call after rotating the keypair."""
    global _vapid_instance, _vapid_config
    _vapid_instance = None
    _vapid_config = None


@dataclass(frozen=True)
class SendResult:
    sent: int
    deleted_dead: int
    failed: int


async def send_to_subscription(
    db: AsyncSession,
    sub: PushSubscription,
    payload: dict[str, Any],
    *,
    ttl: int = DEFAULT_TTL,
) -> bool:
    """Send to a single subscription. Returns True on success.

    Side effects:
      - On 404/410 (subscription dead): deletes the row.
      - On any other error: stamps last_failed_at + last_error.
      - On success: stamps last_seen_at.

    Caller is responsible for `await db.commit()` afterwards.
    """
    vapid = await _get_vapid_instance(db)
    if vapid is None:
        logger.warning("Push send skipped — VAPID not configured")
        return False
    vapid_instance, config = vapid

    try:
        webpush(
            subscription_info={
                "endpoint": sub.endpoint,
                "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
            },
            data=json.dumps(payload),
            vapid_private_key=vapid_instance,  # NB: instance, not PEM
            vapid_claims={"sub": config.subject},
            ttl=ttl,
        )
        sub.last_seen_at = datetime.now(timezone.utc)
        sub.last_failed_at = None
        sub.last_error = None
        return True
    except WebPushException as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        # 404/410 = dead subscription. The browser uninstalled / wiped the
        # PWA / the OS revoked permission. Delete now so we stop retrying.
        if status in (404, 410):
            logger.info(
                f"Removing dead push subscription {sub.id} (status={status})"
            )
            await db.execute(
                delete(PushSubscription).where(PushSubscription.id == sub.id)
            )
            return False

        # Anything else — record + move on. Don't crash a multi-recipient
        # send because one device is broken.
        sub.last_failed_at = datetime.now(timezone.utc)
        sub.last_error = (str(e) or repr(e))[:400]
        logger.warning(
            f"Push send failed for sub {sub.id} (status={status}): {e}"
        )
        return False
    except Exception as e:
        sub.last_failed_at = datetime.now(timezone.utc)
        sub.last_error = (str(e) or repr(e))[:400]
        logger.exception(f"Unexpected error sending push to sub {sub.id}")
        return False


async def send_to_user(
    db: AsyncSession,
    user_id: uuid.UUID,
    payload: dict[str, Any],
) -> SendResult:
    """Send a notification to every subscription belonging to a user.

    A user may have multiple subscriptions (phone, tablet, laptop). We send
    to all of them. Returns counts for instrumentation.
    """
    result = await db.execute(
        select(PushSubscription).where(
            PushSubscription.user_id == user_id,
            PushSubscription.deleted_at.is_(None),
        )
    )
    subs = list(result.scalars().all())

    sent = 0
    failed = 0
    dead = 0
    for sub in subs:
        before_id = sub.id
        ok = await send_to_subscription(db, sub, payload)
        if ok:
            sent += 1
        else:
            # If the row was deleted, send_to_subscription already removed it.
            # Check by re-querying — cheap because of the index.
            check = await db.execute(
                select(PushSubscription.id).where(PushSubscription.id == before_id)
            )
            if check.scalar_one_or_none() is None:
                dead += 1
            else:
                failed += 1

    await db.flush()
    return SendResult(sent=sent, deleted_dead=dead, failed=failed)


async def send_to_users(
    db: AsyncSession,
    user_ids: list[uuid.UUID],
    payload: dict[str, Any],
) -> SendResult:
    """Batch send to many users (e.g. announcement to all parents)."""
    total = SendResult(sent=0, deleted_dead=0, failed=0)
    for uid in user_ids:
        r = await send_to_user(db, uid, payload)
        total = SendResult(
            sent=total.sent + r.sent,
            deleted_dead=total.deleted_dead + r.deleted_dead,
            failed=total.failed + r.failed,
        )
    return total


# Helper used by the API endpoint to upsert a subscription
async def upsert_subscription(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    endpoint: str,
    p256dh: str,
    auth: str,
    user_agent: str | None = None,
) -> PushSubscription:
    """Insert or refresh a subscription. Endpoint is the unique key.

    Re-subscribing from the same device returns the same endpoint, so we
    just bump last_seen_at and clear any prior failure state.
    """
    result = await db.execute(
        select(PushSubscription).where(PushSubscription.endpoint == endpoint)
    )
    row = result.scalar_one_or_none()

    if row:
        # Refresh — same device, possibly different user (uncommon but
        # possible if two users share a phone). Update everything.
        row.tenant_id = tenant_id
        row.user_id = user_id
        row.p256dh = p256dh
        row.auth = auth
        row.user_agent = user_agent
        row.last_seen_at = datetime.now(timezone.utc)
        row.last_failed_at = None
        row.last_error = None
        row.deleted_at = None
        await db.flush()
        return row

    sub = PushSubscription(
        tenant_id=tenant_id,
        user_id=user_id,
        endpoint=endpoint,
        p256dh=p256dh,
        auth=auth,
        user_agent=user_agent,
    )
    db.add(sub)
    await db.flush()
    return sub


async def delete_subscription_by_endpoint(
    db: AsyncSession, *, user_id: uuid.UUID, endpoint: str
) -> bool:
    """Remove a subscription. Called when a user disables notifications."""
    result = await db.execute(
        delete(PushSubscription).where(
            PushSubscription.endpoint == endpoint,
            PushSubscription.user_id == user_id,
        )
    )
    return (result.rowcount or 0) > 0


async def list_user_subscriptions(
    db: AsyncSession, user_id: uuid.UUID
) -> list[PushSubscription]:
    """Return all active subscriptions for a user (one per device)."""
    result = await db.execute(
        select(PushSubscription)
        .where(
            PushSubscription.user_id == user_id,
            PushSubscription.deleted_at.is_(None),
        )
        .order_by(PushSubscription.last_seen_at.desc())
    )
    return list(result.scalars().all())


# ============================================================================
# Super-admin key management — mirrors scripts/generate_vapid_keys.py so it
# can be called from a UI route instead of requiring shell access.
# ============================================================================

def _generate_keypair(subject: str) -> dict[str, str]:
    """Produce a fresh P-256 keypair in the exact formats pywebpush + browsers
    expect. Pure function — no DB write. See scripts/generate_vapid_keys.py
    for the canonical comments on why SEC1 / X9.62 is the right encoding.
    """
    priv = ec.generate_private_key(ec.SECP256R1(), default_backend())

    pub_bytes = priv.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    public_b64url = base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode("ascii")

    private_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")

    return {
        "public_key_b64url": public_b64url,
        "private_pem": private_pem,
        "subject": subject,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def generate_and_store_keypair(
    db: AsyncSession,
    *,
    subject: str,
    force: bool = False,
) -> dict[str, str]:
    """Create a VAPID keypair and persist to system_settings.vapid_config.

    Args:
        subject: VAPID `sub` claim, e.g. ``mailto:admin@your-domain.com``.
                 Push services use this to contact you about delivery issues.
        force:   When True, overwrites an existing keypair. This INVALIDATES
                 every existing push subscription — affected users see their
                 device drop to "Off" and must re-enable.

    Raises:
        ValueError: keypair already exists and force=False.

    Returns:
        The stored config dict (without secrets — the PEM stays in the DB).
    """
    if not subject or "@" not in subject:
        raise ValueError("Subject must look like 'mailto:user@host'")

    result = await db.execute(
        select(SystemSettings).where(SystemSettings.key == VAPID_SETTINGS_KEY)
    )
    row = result.scalar_one_or_none()

    if row and row.value and (row.value.get("public_key_b64url") or "").strip() and not force:
        raise ValueError(
            "A VAPID keypair already exists. Use rotate=True to overwrite "
            "(invalidates every existing subscription)."
        )

    config = _generate_keypair(subject)

    if row:
        row.value = config
    else:
        db.add(SystemSettings(key=VAPID_SETTINGS_KEY, value=config))

    await db.flush()
    reset_cache()  # next send refreshes from the new keypair

    logger.info(
        f"VAPID keypair {'rotated' if force else 'generated'} "
        f"(subject={subject}, public_key={config['public_key_b64url'][:16]}...)"
    )

    # Return WITHOUT the private PEM — admin UI never needs to display it
    return {
        "public_key_b64url": config["public_key_b64url"],
        "subject": config["subject"],
        "generated_at": config["generated_at"],
    }


async def update_subject(db: AsyncSession, subject: str) -> str:
    """Change just the VAPID subject (contact email) without rotating keys."""
    if not subject or "@" not in subject:
        raise ValueError("Subject must look like 'mailto:user@host'")
    result = await db.execute(
        select(SystemSettings).where(SystemSettings.key == VAPID_SETTINGS_KEY)
    )
    row = result.scalar_one_or_none()
    if not row or not row.value:
        raise ValueError("Generate a keypair first before setting the subject.")
    new_value = dict(row.value)
    new_value["subject"] = subject
    row.value = new_value
    await db.flush()
    reset_cache()
    return subject


async def count_subscriptions(db: AsyncSession) -> dict[str, int]:
    """Platform-wide subscription stats for the admin dashboard."""
    total = await db.execute(
        select(func.count(PushSubscription.id)).where(
            PushSubscription.deleted_at.is_(None)
        )
    )
    by_tenant = await db.execute(
        select(func.count(func.distinct(PushSubscription.tenant_id))).where(
            PushSubscription.deleted_at.is_(None)
        )
    )
    failing = await db.execute(
        select(func.count(PushSubscription.id)).where(
            PushSubscription.deleted_at.is_(None),
            PushSubscription.last_failed_at.is_not(None),
        )
    )
    return {
        "total": int(total.scalar() or 0),
        "tenants_with_subscriptions": int(by_tenant.scalar() or 0),
        "currently_failing": int(failing.scalar() or 0),
    }
