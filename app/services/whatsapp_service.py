"""WhatsApp service using Meta Cloud API for two-way messaging.

Config precedence:
  1. system_settings.whatsapp_config (managed via /admin/whatsapp-settings)
  2. Environment variables (WHATSAPP_*, kept for backward-compat + local dev)

Runtime code that needs credentials should call `get_config(db)`. The
per-instance sync attributes (`self.phone_number_id` etc.) exist only for
callers that construct the service without a DB session — they fall back
to env vars alone.
"""

import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import SystemSettings

logger = logging.getLogger(__name__)
settings = get_settings()

WHATSAPP_SETTINGS_KEY = "whatsapp_config"
MASKED = "********"


@dataclass(frozen=True)
class WhatsAppConfig:
    """Resolved WhatsApp credentials — from DB if set, else env vars."""
    phone_number_id: str
    business_account_id: str
    access_token: str
    verify_token: str
    app_secret: str

    @property
    def configured(self) -> bool:
        return bool(
            self.phone_number_id
            and self.access_token
            and self.verify_token
        )

    def with_masked_secrets(self) -> dict[str, Any]:
        """Serialise for GET responses — never expose real access_token/app_secret."""
        return {
            "phone_number_id": self.phone_number_id,
            "business_account_id": self.business_account_id,
            "access_token": MASKED if self.access_token else "",
            "verify_token": self.verify_token,  # not a secret — Meta echoes it
            "app_secret": MASKED if self.app_secret else "",
            "configured": self.configured,
        }


async def get_config(db: AsyncSession) -> WhatsAppConfig:
    """Return the effective WhatsApp config for this instance.

    DB values take precedence; env vars fill in anything the admin hasn't
    set via the UI yet. Callers that just need to know "is WhatsApp on?"
    can check `.configured`.
    """
    result = await db.execute(
        select(SystemSettings).where(SystemSettings.key == WHATSAPP_SETTINGS_KEY)
    )
    row = result.scalar_one_or_none()
    v = (row.value if row else None) or {}
    return WhatsAppConfig(
        phone_number_id=str(v.get("phone_number_id") or settings.whatsapp_phone_number_id or "").strip(),
        business_account_id=str(v.get("business_account_id") or settings.whatsapp_business_account_id or "").strip(),
        access_token=str(v.get("access_token") or settings.whatsapp_access_token or "").strip(),
        verify_token=str(v.get("verify_token") or settings.whatsapp_verify_token or "").strip(),
        # Historical fallback: `app_secret_key` was reused for HMAC verification
        # before this refactor. Keep that fallback so existing deployments
        # don't break if the admin hasn't set app_secret via the UI yet.
        app_secret=str(v.get("app_secret") or settings.app_secret_key or "").strip(),
    )


async def save_config(db: AsyncSession, updates: dict[str, Any]) -> WhatsAppConfig:
    """Upsert the WhatsApp config. MASKED placeholders preserve existing
    secret values (email_settings + payment_gateway pattern)."""
    existing = await get_config(db)

    allowed = {"phone_number_id", "business_account_id", "access_token",
               "verify_token", "app_secret"}
    clean = {k: str(v or "").strip() for k, v in (updates or {}).items() if k in allowed}

    # Preserve secrets when the admin sent MASKED (means "don't change")
    for secret_key in ("access_token", "app_secret"):
        if clean.get(secret_key) == MASKED:
            clean[secret_key] = getattr(existing, secret_key)

    row_result = await db.execute(
        select(SystemSettings).where(SystemSettings.key == WHATSAPP_SETTINGS_KEY)
    )
    row = row_result.scalar_one_or_none()
    merged = {**(row.value if row else {}), **clean}
    if row:
        row.value = merged
    else:
        db.add(SystemSettings(key=WHATSAPP_SETTINGS_KEY, value=merged))
    await db.flush()
    logger.info(
        f"WhatsApp config saved (phone_number_id set: {bool(merged.get('phone_number_id'))}, "
        f"access_token set: {bool(merged.get('access_token'))})"
    )
    return await get_config(db)


class WhatsAppService:
    """Service for sending and receiving WhatsApp messages via Meta Cloud API.

    When constructed without a DB session, falls back to env vars. Prefer
    calling `service.with_config(cfg)` when you already have a WhatsAppConfig
    in hand — avoids one round-trip.
    """

    def __init__(self, config: WhatsAppConfig | None = None):
        """Initialize the WhatsApp service. Pass a WhatsAppConfig to use DB
        settings; omit to fall back to env vars."""
        self.api_url = settings.whatsapp_api_url
        if config is not None:
            self._apply_config(config)
        else:
            # Env-only fallback (matches the original behaviour)
            self.phone_number_id = settings.whatsapp_phone_number_id
            self.business_account_id = settings.whatsapp_business_account_id
            self.access_token = settings.whatsapp_access_token
            self.verify_token = settings.whatsapp_verify_token
            self.app_secret = settings.app_secret_key

    def _apply_config(self, cfg: WhatsAppConfig) -> None:
        self.phone_number_id = cfg.phone_number_id
        self.business_account_id = cfg.business_account_id
        self.access_token = cfg.access_token
        self.verify_token = cfg.verify_token
        self.app_secret = cfg.app_secret

    def with_config(self, cfg: WhatsAppConfig) -> "WhatsAppService":
        """Return self after applying a fresh config. Fluent so callers can
        chain: `WhatsAppService().with_config(await get_config(db))`."""
        self._apply_config(cfg)
        return self

    @property
    def is_configured(self) -> bool:
        """Check if WhatsApp integration is properly configured."""
        return bool(
            self.phone_number_id
            and self.access_token
            and self.verify_token
        )

    def verify_webhook_signature(
        self,
        payload: bytes,
        signature: str,
    ) -> bool:
        """
        Verify the HMAC signature from Meta webhook request.

        Uses the app_secret configured via /admin/whatsapp-settings (not the
        generic app_secret_key). Falls back to app_secret_key only if the
        admin hasn't set app_secret yet — preserves the pre-refactor default.

        Args:
            payload: Raw request body bytes
            signature: X-Hub-Signature-256 header value

        Returns:
            True if signature is valid
        """
        if not signature.startswith("sha256="):
            return False

        expected_signature = signature[7:]  # Remove "sha256=" prefix
        secret = self.app_secret or settings.app_secret_key

        computed_signature = hmac.new(
            secret.encode(),
            payload,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected_signature, computed_signature)

    async def test_connection(self) -> tuple[bool, str]:
        """Verify credentials against Meta's phone-number info endpoint.

        Cheap, safe, doesn't send any message. If Meta accepts the access
        token + phone_number_id, we return (True, phone_display_number).
        """
        if not self.phone_number_id:
            return False, "phone_number_id is not set."
        if not self.access_token:
            return False, "access_token is not set."

        url = f"{self.api_url}/{self.phone_number_id}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {self.access_token}"},
                )
        except httpx.HTTPError as e:
            return False, f"Could not reach Meta: {e}"

        if resp.status_code == 200:
            data = resp.json()
            display = data.get("display_phone_number", "(no display number)")
            name = data.get("verified_name", "")
            return True, f"OK — connected to {display}" + (f" ({name})" if name else "")

        if resp.status_code == 401:
            return False, "Access token rejected (401). Regenerate in Meta Business Manager."
        if resp.status_code == 404:
            return False, "phone_number_id not found (404). Check the ID from Meta."
        try:
            err = resp.json().get("error", {}).get("message", "")
        except Exception:
            err = resp.text[:200]
        return False, f"Meta returned {resp.status_code}: {err}"

    async def send_template_message(
        self,
        to_phone: str,
        template_name: str,
        language_code: str,
        parameters: list[str] | None = None,
    ) -> dict | None:
        """
        Send a pre-approved template message.

        Args:
            to_phone: Recipient phone number in E.164 format (e.g., +27821234567)
            template_name: Name of the approved template
            language_code: Template language code (e.g., "en", "en_US")
            parameters: Template variable values

        Returns:
            API response dict or None if failed
        """
        if not self.is_configured:
            logger.warning("WhatsApp not configured, skipping message")
            return None

        # Clean phone number - ensure E.164 format without +
        clean_phone = to_phone.lstrip("+")

        # Build template components
        components = []
        if parameters:
            components.append({
                "type": "body",
                "parameters": [
                    {"type": "text", "text": str(p)} for p in parameters
                ],
            })

        payload = {
            "messaging_product": "whatsapp",
            "to": clean_phone,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language_code},
            },
        }

        if components:
            payload["template"]["components"] = components

        return await self._send_request(payload)

    async def send_text_message(
        self,
        to_phone: str,
        body: str,
        preview_url: bool = False,
    ) -> dict | None:
        """
        Send a free-form text message.

        Note: Only works within 24-hour conversation window after user initiates.

        Args:
            to_phone: Recipient phone number in E.164 format
            body: Message text
            preview_url: Whether to show URL previews

        Returns:
            API response dict or None if failed
        """
        if not self.is_configured:
            logger.warning("WhatsApp not configured, skipping message")
            return None

        clean_phone = to_phone.lstrip("+")

        payload = {
            "messaging_product": "whatsapp",
            "to": clean_phone,
            "type": "text",
            "text": {
                "body": body,
                "preview_url": preview_url,
            },
        }

        return await self._send_request(payload)

    async def send_interactive_buttons(
        self,
        to_phone: str,
        body: str,
        buttons: list[dict[str, str]],
        header: str | None = None,
        footer: str | None = None,
    ) -> dict | None:
        """Send a WhatsApp interactive message with up to 3 quick-reply buttons.

        Args:
            to_phone: Recipient phone number in E.164 format
            body: Main message text (max 1024 chars)
            buttons: List of ``{"id": ..., "title": ...}`` — max 3.
                Title max 20 chars; ID max 256 chars (we use it to encode the
                menu state for the state machine).
            header: Optional bold header above the body (max 60 chars).
            footer: Optional footer below the body (max 60 chars).

        Meta rejects the message with 400 if any of the caps are exceeded, so
        we truncate defensively — a truncated button title still works, a
        400 doesn't.
        """
        if not self.is_configured:
            logger.warning("WhatsApp not configured, skipping message")
            return None

        if not buttons:
            raise ValueError("At least one button is required")
        if len(buttons) > 3:
            raise ValueError("WhatsApp allows at most 3 interactive buttons")

        clean_phone = to_phone.lstrip("+")
        interactive: dict = {
            "type": "button",
            "body": {"text": body[:1024]},
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": str(b["id"])[:256],
                            "title": str(b["title"])[:20],
                        },
                    }
                    for b in buttons
                ]
            },
        }
        if header:
            interactive["header"] = {"type": "text", "text": header[:60]}
        if footer:
            interactive["footer"] = {"text": footer[:60]}

        return await self._send_request({
            "messaging_product": "whatsapp",
            "to": clean_phone,
            "type": "interactive",
            "interactive": interactive,
        })

    async def send_interactive_list(
        self,
        to_phone: str,
        body: str,
        button_text: str,
        sections: list[dict],
        header: str | None = None,
        footer: str | None = None,
    ) -> dict | None:
        """Send a WhatsApp interactive list message.

        Lists are the right choice when you have more than 3 options — the
        user taps the ``button_text`` button to expand a modal with up to
        10 rows across one or more sections.

        Args:
            to_phone: Recipient phone number in E.164 format
            body: Main message text (max 1024 chars)
            button_text: The button label that opens the list (max 20 chars)
            sections: [{"title": "...", "rows": [{"id": ..., "title": ...,
                "description": ...}, ...]}, ...] — max 10 rows total across
                all sections. Row title max 24 chars, description max 72,
                id max 200. Section title optional but recommended if >1.
            header: Optional bold header (max 60 chars).
            footer: Optional footer (max 60 chars).
        """
        if not self.is_configured:
            logger.warning("WhatsApp not configured, skipping message")
            return None

        total_rows = sum(len(s.get("rows", [])) for s in sections)
        if total_rows == 0:
            raise ValueError("List message needs at least one row")
        if total_rows > 10:
            raise ValueError("WhatsApp allows at most 10 rows in a list")

        clean_phone = to_phone.lstrip("+")
        interactive: dict = {
            "type": "list",
            "body": {"text": body[:1024]},
            "action": {
                "button": button_text[:20],
                "sections": [
                    {
                        "title": (s.get("title") or "")[:24],
                        "rows": [
                            {
                                "id": str(r["id"])[:200],
                                "title": str(r["title"])[:24],
                                "description": str(r.get("description") or "")[:72],
                            }
                            for r in s.get("rows", [])
                        ],
                    }
                    for s in sections
                ],
            },
        }
        if header:
            interactive["header"] = {"type": "text", "text": header[:60]}
        if footer:
            interactive["footer"] = {"text": footer[:60]}

        return await self._send_request({
            "messaging_product": "whatsapp",
            "to": clean_phone,
            "type": "interactive",
            "interactive": interactive,
        })

    async def _send_request(self, payload: dict) -> dict | None:
        """Send a request to the WhatsApp API."""
        url = f"{self.api_url}/{self.phone_number_id}/messages"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.access_token}",
                        "Content-Type": "application/json",
                    },
                )

                if response.status_code == 200:
                    result = response.json()
                    logger.info(f"WhatsApp message sent: {result.get('messages', [{}])[0].get('id')}")
                    return result
                else:
                    logger.error(f"WhatsApp API error: {response.status_code} - {response.text}")
                    return None

        except Exception as e:
            logger.error(f"Failed to send WhatsApp message: {e}")
            return None

    def parse_webhook_message(self, body: dict) -> list[dict]:
        """
        Parse incoming webhook payload and extract messages.

        Args:
            body: Webhook request body

        Returns:
            List of parsed message dicts with: from_phone, message_type, text, timestamp
        """
        messages = []

        try:
            entries = body.get("entry", [])
            for entry in entries:
                changes = entry.get("changes", [])
                for change in changes:
                    value = change.get("value", {})
                    incoming_messages = value.get("messages", [])

                    for msg in incoming_messages:
                        parsed = {
                            "from_phone": msg.get("from"),
                            "message_type": msg.get("type"),
                            "timestamp": msg.get("timestamp"),
                            "message_id": msg.get("id"),
                            # ``interactive_id`` is the developer-set id from
                            # the button or list row the user tapped — this
                            # is what the bot state machine dispatches on.
                            # ``text`` alone is the human-readable title.
                            "interactive_id": None,
                        }

                        # Extract text based on message type
                        if msg.get("type") == "text":
                            parsed["text"] = msg.get("text", {}).get("body", "")
                        elif msg.get("type") == "button":
                            # Template quick-reply button (not the new
                            # interactive button API — templates send this).
                            parsed["text"] = msg.get("button", {}).get("text", "")
                            parsed["interactive_id"] = msg.get("button", {}).get("payload")
                        elif msg.get("type") == "interactive":
                            interactive = msg.get("interactive", {})
                            if interactive.get("type") == "button_reply":
                                reply = interactive.get("button_reply", {})
                                parsed["text"] = reply.get("title", "")
                                parsed["interactive_id"] = reply.get("id")
                            elif interactive.get("type") == "list_reply":
                                reply = interactive.get("list_reply", {})
                                parsed["text"] = reply.get("title", "")
                                parsed["interactive_id"] = reply.get("id")
                        else:
                            parsed["text"] = f"[{msg.get('type')} message]"

                        messages.append(parsed)

        except Exception as e:
            logger.error(f"Error parsing WhatsApp webhook: {e}")

        return messages

    # ============== Template Message Helpers ==============

    async def send_attendance_alert(
        self,
        to_phone: str,
        child_name: str,
        status: str,
        school_name: str,
        language: str = "en",
    ) -> dict | None:
        """Send an attendance alert template message."""
        return await self.send_template_message(
            to_phone=to_phone,
            template_name="attendance_alert",
            language_code=language,
            parameters=[child_name, status, school_name],
        )

    async def send_report_ready(
        self,
        to_phone: str,
        report_type: str,
        child_name: str,
        url: str,
        language: str = "en",
    ) -> dict | None:
        """Send a report ready template message."""
        return await self.send_template_message(
            to_phone=to_phone,
            template_name="report_ready",
            language_code=language,
            parameters=[report_type, child_name, url],
        )

    async def send_announcement(
        self,
        to_phone: str,
        school_name: str,
        subject: str,
        language: str = "en",
    ) -> dict | None:
        """Send an announcement template message."""
        return await self.send_template_message(
            to_phone=to_phone,
            template_name="announcement",
            language_code=language,
            parameters=[school_name, subject],
        )

    async def send_parent_invite(
        self,
        to_phone: str,
        school_name: str,
        code: str,
        language: str = "en",
    ) -> dict | None:
        """Send a parent invitation template message."""
        return await self.send_template_message(
            to_phone=to_phone,
            template_name="parent_invite",
            language_code=language,
            parameters=[school_name, code],
        )

    async def send_welcome(
        self,
        to_phone: str,
        school_name: str,
        url: str,
        language: str = "en",
    ) -> dict | None:
        """Send a welcome template message."""
        return await self.send_template_message(
            to_phone=to_phone,
            template_name="welcome",
            language_code=language,
            parameters=[school_name, url],
        )


# Singleton instance — env-var config only, kept for backward-compat with
# existing callers that don't have a DB session in scope.
_whatsapp_service: WhatsAppService | None = None


def get_whatsapp_service() -> WhatsAppService:
    """Get an env-var-only WhatsApp service singleton.

    Callers that need the DB-managed config (which the admin sets via the
    /admin/whatsapp-settings page) should use `get_whatsapp_service_from_db(db)`
    instead — that hydrates the singleton with the latest settings.
    """
    global _whatsapp_service
    if _whatsapp_service is None:
        _whatsapp_service = WhatsAppService()
    return _whatsapp_service


async def get_whatsapp_service_from_db(db: AsyncSession) -> WhatsAppService:
    """Return a WhatsAppService loaded with the DB-managed config.

    Use this everywhere the admin's UI-configured credentials should take
    precedence over env vars — webhook signature checks, outbound sends
    from the admin test page, the eventual bot flow handlers.
    """
    cfg = await get_config(db)
    return WhatsAppService(cfg)
