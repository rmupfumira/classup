"""WhatsApp service using Meta Cloud API for two-way messaging."""

import hashlib
import hmac
import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class WhatsAppService:
    """Service for sending and receiving WhatsApp messages via Meta Cloud API."""

    def __init__(self):
        """Initialize the WhatsApp service."""
        self.api_url = settings.whatsapp_api_url
        self.phone_number_id = settings.whatsapp_phone_number_id
        self.access_token = settings.whatsapp_access_token
        self.verify_token = settings.whatsapp_verify_token

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

        Args:
            payload: Raw request body bytes
            signature: X-Hub-Signature-256 header value

        Returns:
            True if signature is valid
        """
        if not signature.startswith("sha256="):
            return False

        expected_signature = signature[7:]  # Remove "sha256=" prefix

        computed_signature = hmac.new(
            settings.app_secret_key.encode(),
            payload,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected_signature, computed_signature)

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
                        }

                        # Extract text based on message type
                        if msg.get("type") == "text":
                            parsed["text"] = msg.get("text", {}).get("body", "")
                        elif msg.get("type") == "button":
                            parsed["text"] = msg.get("button", {}).get("text", "")
                        elif msg.get("type") == "interactive":
                            interactive = msg.get("interactive", {})
                            if interactive.get("type") == "button_reply":
                                parsed["text"] = interactive.get("button_reply", {}).get("title", "")
                            elif interactive.get("type") == "list_reply":
                                parsed["text"] = interactive.get("list_reply", {}).get("title", "")
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


# Singleton instance
_whatsapp_service: WhatsAppService | None = None


def get_whatsapp_service() -> WhatsAppService:
    """Get the WhatsApp service singleton."""
    global _whatsapp_service
    if _whatsapp_service is None:
        _whatsapp_service = WhatsAppService()
    return _whatsapp_service
