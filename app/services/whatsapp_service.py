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
        *,
        header_document_media_id: str | None = None,
        header_document_filename: str | None = None,
        button_url_variables: list[str] | None = None,
    ) -> dict | None:
        """Send a pre-approved template message.

        Args:
            to_phone: Recipient phone number in E.164 format (e.g. +27821234567).
            template_name: Name of the approved template.
            language_code: Template language code (e.g. "en", "en_US").
            parameters: Template BODY variable values, in order.
            header_document_media_id: Media ID (from upload_media()) for a
                template that has a DOCUMENT header — used by templates like
                ``purchase_receipt_3`` where the invoice PDF sits as the
                header attachment. Requires the template to be authored
                with a document header on Meta's side.
            header_document_filename: Filename shown for the header
                document attachment (e.g. "invoice_INV-2026-0001.pdf").
                Optional but strongly recommended for UX.
            button_url_variables: Values to fill dynamic-URL button slots
                (``{{1}}`` in the button's URL configured on Meta). One
                value per button component that has a dynamic URL. Order
                matches the button order defined in the template.

        Returns:
            API response dict, or None if WhatsApp isn't configured.
        """
        if not self.is_configured:
            logger.warning("WhatsApp not configured, skipping message")
            return None

        clean_phone = to_phone.lstrip("+")

        # Build components in the shape Meta's Cloud API expects.
        # Order in the list doesn't matter — Meta reads by `type`.
        components: list[dict] = []

        # HEADER — document attachment for templates like purchase_receipt.
        # Meta requires the media_id to have been uploaded to *the same
        # phone number* the message is sent from, which upload_media()
        # already handles.
        if header_document_media_id:
            document_param: dict = {"id": header_document_media_id}
            if header_document_filename:
                document_param["filename"] = header_document_filename
            components.append({
                "type": "header",
                "parameters": [
                    {"type": "document", "document": document_param},
                ],
            })

        # BODY — variable substitution, unchanged.
        if parameters:
            components.append({
                "type": "body",
                "parameters": [
                    {"type": "text", "text": str(p)} for p in parameters
                ],
            })

        # BUTTONS — one component per dynamic-URL button, with `index` as
        # its position in the template. Static buttons (fixed URL, phone
        # dial) don't need any parameter — they're baked into the template.
        for i, url_value in enumerate(button_url_variables or []):
            components.append({
                "type": "button",
                "sub_type": "url",
                "index": str(i),
                "parameters": [
                    {"type": "text", "text": str(url_value)},
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

    # ============== Media upload + send helpers ==============
    #
    # WhatsApp Cloud API supports two ways to attach media (docs/images/etc):
    #   1. Public URL — Meta fetches the file itself. Requires the URL to be
    #      publicly accessible, which is a leak risk for tenant data.
    #   2. Media API — POST bytes to /media first, get a media_id, then
    #      reference the id in the message. No public URL exposed.
    #
    # We ALWAYS use path 2. Uploads are per-phone_number_id, so cross-tenant
    # media_id leakage is impossible even if an id somehow escaped — Meta
    # scopes visibility to the WABA that uploaded it.

    # Enforced client-side BEFORE the network call — cheaper to reject
    # oversized attachments here than to eat the round-trip and Meta 400.
    MAX_DOCUMENT_BYTES = 100 * 1024 * 1024   # WhatsApp doc limit
    MAX_IMAGE_BYTES    =   5 * 1024 * 1024   # WhatsApp image limit

    _ALLOWED_DOC_MIMES = frozenset({
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",   # .docx
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",         # .xlsx
        "application/vnd.openxmlformats-officedocument.presentationml.presentation", # .pptx
        "application/msword",                # .doc
        "application/vnd.ms-excel",          # .xls
        "text/plain",
    })
    _ALLOWED_IMAGE_MIMES = frozenset({"image/jpeg", "image/png"})

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        """Strip path traversal + control chars. WhatsApp displays this
        filename verbatim to the recipient — we own its integrity."""
        import os
        # Take basename only — kills any '../' or 'C:\...' traversal.
        cleaned = os.path.basename(name).strip()
        # Belt-and-braces: replace any remaining path/control chars.
        cleaned = "".join(
            c for c in cleaned
            if c not in ('/', '\\', '\0') and (c.isprintable() or c == ' ')
        )
        # Never send an empty filename — Meta rejects it and it looks broken.
        return cleaned or "document.pdf"

    async def upload_media(
        self,
        file_bytes: bytes,
        mime_type: str,
        filename: str | None = None,
    ) -> str | None:
        """Upload a media file to Meta's Media API, return the media_id.

        The returned id is what ``send_document_message`` / ``send_image_message``
        reference. Meta expires it in ~30 days (long enough for any single
        message flow). No public URL is ever exposed.

        Returns None if the service isn't configured, the file exceeds the
        size cap, or Meta rejects the upload — the caller must handle None
        gracefully (typically by falling back to a text message).
        """
        if not self.is_configured:
            logger.warning("WhatsApp not configured, skipping media upload")
            return None

        # Size checks — enforced client-side to avoid burning bandwidth
        # + a guaranteed Meta 400 on files above the cap.
        if mime_type in self._ALLOWED_IMAGE_MIMES:
            cap = self.MAX_IMAGE_BYTES
            kind = "image"
        elif mime_type in self._ALLOWED_DOC_MIMES:
            cap = self.MAX_DOCUMENT_BYTES
            kind = "document"
        else:
            logger.warning(
                "Rejected upload — mime_type %s is not on the WhatsApp allow list",
                mime_type,
            )
            return None

        if len(file_bytes) > cap:
            logger.warning(
                "Rejected upload — %s file is %d bytes, cap is %d",
                kind, len(file_bytes), cap,
            )
            return None

        safe_name = self._sanitize_filename(filename or f"file.{mime_type.split('/')[-1]}")
        url = f"{self.api_url}/{self.phone_number_id}/media"

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                # httpx multipart: fields for each form part, files= for the
                # binary. Meta expects messaging_product=whatsapp on every
                # upload — do NOT omit it.
                resp = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {self.access_token}"},
                    data={
                        "messaging_product": "whatsapp",
                        "type": mime_type,
                    },
                    files={"file": (safe_name, file_bytes, mime_type)},
                )
        except httpx.HTTPError as e:
            logger.error("WhatsApp media upload failed: %s", e)
            return None

        if resp.status_code == 200:
            media_id = resp.json().get("id")
            logger.info(
                "WhatsApp media uploaded: id=%s type=%s size=%d filename=%s",
                media_id, mime_type, len(file_bytes), safe_name,
            )
            return media_id

        logger.error(
            "WhatsApp media upload rejected: %s - %s",
            resp.status_code, resp.text[:400],
        )
        return None

    async def send_document_message(
        self,
        to_phone: str,
        media_id: str,
        filename: str,
        caption: str | None = None,
    ) -> dict | None:
        """Send a document (PDF / DOCX / XLSX etc.) referenced by media_id.

        The recipient sees a document bubble in WhatsApp with the filename
        + tap-to-download / open. Caption text renders below the bubble.

        Requires the parent has messaged in the last 24hr (session window)
        — outside that, WhatsApp only permits pre-approved templates and
        this call will fail with a 24hr window error.
        """
        if not self.is_configured:
            return None
        payload = {
            "messaging_product": "whatsapp",
            "to": to_phone.lstrip("+"),
            "type": "document",
            "document": {
                "id": media_id,
                "filename": self._sanitize_filename(filename),
            },
        }
        if caption:
            payload["document"]["caption"] = caption[:1024]
        return await self._send_request(payload)

    async def send_image_message(
        self,
        to_phone: str,
        media_id: str,
        caption: str | None = None,
    ) -> dict | None:
        """Send an image (JPG/PNG) referenced by media_id.

        Renders inline in WhatsApp with tap-to-view-fullscreen. Caption
        appears below the image.
        """
        if not self.is_configured:
            return None
        payload = {
            "messaging_product": "whatsapp",
            "to": to_phone.lstrip("+"),
            "type": "image",
            "image": {"id": media_id},
        }
        if caption:
            payload["image"]["caption"] = caption[:1024]
        return await self._send_request(payload)

    async def send_document_from_bytes(
        self,
        to_phone: str,
        file_bytes: bytes,
        mime_type: str,
        filename: str,
        caption: str | None = None,
    ) -> dict | None:
        """Convenience: upload bytes + send in one call. Returns None if
        either step fails. This is what most callers actually want —
        they have file bytes in hand and don't care about the media_id."""
        media_id = await self.upload_media(file_bytes, mime_type, filename)
        if not media_id:
            return None
        return await self.send_document_message(
            to_phone=to_phone, media_id=media_id,
            filename=filename, caption=caption,
        )

    async def send_image_from_bytes(
        self,
        to_phone: str,
        image_bytes: bytes,
        mime_type: str,
        caption: str | None = None,
    ) -> dict | None:
        """Convenience: upload bytes + send in one call for images."""
        media_id = await self.upload_media(image_bytes, mime_type, filename="image")
        if not media_id:
            return None
        return await self.send_image_message(
            to_phone=to_phone, media_id=media_id, caption=caption,
        )

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

    # ─────────────────── Payment / billing templates ───────────────────
    # These use Meta gallery templates as the design base (utility
    # category, fast approval). All three carry a dynamic URL button
    # that deep-links to the invoice view in ClassUp.

    async def send_invoice_sent(
        self,
        to_phone: str,
        *,
        parent_name: str,
        invoice_number: str,
        student_name: str,
        formatted_amount: str,
        due_date: str,
        invoice_id: str,
        language: str = "en",
    ) -> dict | None:
        """Notify a parent that a new invoice has been issued.

        Template body (invoice_sent, approved by Meta):
          "Hi {{1}}, a new invoice has been issued for {{2}}.
           Invoice number: {{3}}. Total amount due: {{4}}.
           Please settle by {{5}}. Tap the button below to view
           the full invoice and banking details."
        Button: URL — `/billing/invoices/{{1}}` (dynamic suffix).

        No PDF header — email still carries the PDF; WhatsApp is the
        "you've got an invoice, tap to view" nudge.
        """
        return await self.send_template_message(
            to_phone=to_phone,
            template_name="invoice_sent",
            language_code=language,
            parameters=[parent_name, student_name, invoice_number, formatted_amount, due_date],
            button_url_variables=[invoice_id],
        )

    async def send_payment_received(
        self,
        to_phone: str,
        *,
        parent_name: str,
        formatted_amount: str,
        student_name: str,
        invoice_number: str,
        payment_date: str,
        invoice_id: str,
        language: str = "en",
    ) -> dict | None:
        """Confirm receipt of a parent's payment.

        Template body (payment_received, from payment_successful gallery):
          "Hi {{1}}, your payment of {{2}} for {{3}} ({{4}}) has been
           received on {{5}}. Thank you!"
        Button: URL — `/billing/invoices/{{1}}` (Receipt).
        """
        return await self.send_template_message(
            to_phone=to_phone,
            template_name="payment_received",
            language_code=language,
            parameters=[parent_name, formatted_amount, student_name, invoice_number, payment_date],
            button_url_variables=[invoice_id],
        )

    # ─────────────────── Event templates ───────────────────
    # Based on Meta's gallery templates:
    #   event_invite → event_details_reminder_1
    #   event_reminder → event_details_reminder_2
    #   event_rsvp_confirmed → event_rsvp_confirmation_1

    async def send_event_invited(
        self,
        to_phone: str,
        *,
        event_title: str,
        event_when: str,
        event_location: str | None,
        event_id: str,
        language: str = "en",
    ) -> dict | None:
        """Invite a parent to an event.

        Template body (event_invited, from event_details_reminder_1):
          "You have an upcoming event: {{1}}. Starts on {{2}} at {{3}}."
        Button: URL — /events/{{1}} (dynamic suffix).
        """
        return await self.send_template_message(
            to_phone=to_phone,
            template_name="event_invited",
            language_code=language,
            parameters=[event_title, event_when, event_location or "TBC"],
            button_url_variables=[event_id],
        )

    async def send_event_reminder(
        self,
        to_phone: str,
        *,
        event_title: str,
        event_when: str,
        event_id: str,
        language: str = "en",
    ) -> dict | None:
        """T-24h and T-1h reminder before an event."""
        return await self.send_template_message(
            to_phone=to_phone,
            template_name="event_reminder",
            language_code=language,
            parameters=[event_title, event_when],
            button_url_variables=[event_id],
        )

    async def send_event_rsvp_confirmed(
        self,
        to_phone: str,
        *,
        event_title: str,
        event_when: str,
        response: str,
        event_id: str,
        language: str = "en",
    ) -> dict | None:
        """Confirm a parent's RSVP was received. Sent when a parent
        RSVPs via WhatsApp or from the email link."""
        return await self.send_template_message(
            to_phone=to_phone,
            template_name="event_rsvp_confirmed",
            language_code=language,
            parameters=[event_title, response.title(), event_when],
            button_url_variables=[event_id],
        )

    async def send_invoice_overdue(
        self,
        to_phone: str,
        *,
        student_name: str,
        formatted_balance: str,
        due_date: str,
        invoice_id: str,
        language: str = "en",
    ) -> dict | None:
        """Reminder for an overdue invoice.

        Template body (invoice_overdue, approved by Meta):
          "Payment reminder: an invoice for {{1}} is overdue.
           The amount due is {{2}} and was originally due on {{3}}.
           Please settle as soon as possible to avoid additional
           late fees. If you've already paid, please ignore this
           message."
        Button: URL — `/billing/invoices/{{1}}` (Pay now).

        The penalty phrase is baked into the template — WhatsApp
        approval keeps template phone buttons static, and Meta's
        variable-density check drove us to hard-code the "late fees"
        wording rather than parameterise it.
        """
        return await self.send_template_message(
            to_phone=to_phone,
            template_name="invoice_overdue",
            language_code=language,
            parameters=[student_name, formatted_balance, due_date],
            button_url_variables=[invoice_id],
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
