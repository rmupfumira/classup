"""Webhook service for dispatching events to external endpoints."""

import hashlib
import hmac
import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import WebhookEndpoint, WebhookEvent
from app.utils.tenant_context import get_tenant_id

logger = logging.getLogger(__name__)


class WebhookService:
    """Service for managing webhooks and dispatching events."""

    def _generate_secret(self) -> str:
        """Generate a secure webhook signing secret."""
        return secrets.token_hex(32)

    def _sign_payload(self, payload: str, secret: str) -> str:
        """Sign a payload with HMAC-SHA256."""
        signature = hmac.new(
            secret.encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        return f"sha256={signature}"

    async def create_endpoint(
        self,
        db: AsyncSession,
        url: str,
        events: list[str],
        is_active: bool = True,
    ) -> WebhookEndpoint:
        """Create a new webhook endpoint."""
        tenant_id = get_tenant_id()

        endpoint = WebhookEndpoint(
            tenant_id=tenant_id,
            url=url,
            secret=self._generate_secret(),
            events=events,
            is_active=is_active,
        )

        db.add(endpoint)
        await db.commit()
        await db.refresh(endpoint)

        logger.info(f"Created webhook endpoint {endpoint.id} for {url}")
        return endpoint

    async def get_endpoint(
        self,
        db: AsyncSession,
        endpoint_id: UUID,
    ) -> WebhookEndpoint | None:
        """Get a webhook endpoint by ID."""
        tenant_id = get_tenant_id()

        result = await db.execute(
            select(WebhookEndpoint).where(
                and_(
                    WebhookEndpoint.id == endpoint_id,
                    WebhookEndpoint.tenant_id == tenant_id,
                )
            )
        )
        return result.scalar_one_or_none()

    async def update_endpoint(
        self,
        db: AsyncSession,
        endpoint_id: UUID,
        url: str | None = None,
        events: list[str] | None = None,
        is_active: bool | None = None,
    ) -> WebhookEndpoint | None:
        """Update a webhook endpoint."""
        endpoint = await self.get_endpoint(db, endpoint_id)
        if not endpoint:
            return None

        if url is not None:
            endpoint.url = url
        if events is not None:
            endpoint.events = events
        if is_active is not None:
            endpoint.is_active = is_active

        endpoint.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(endpoint)

        return endpoint

    async def delete_endpoint(
        self,
        db: AsyncSession,
        endpoint_id: UUID,
    ) -> bool:
        """Delete a webhook endpoint."""
        endpoint = await self.get_endpoint(db, endpoint_id)
        if not endpoint:
            return False

        await db.delete(endpoint)
        await db.commit()
        return True

    async def list_endpoints(
        self,
        db: AsyncSession,
    ) -> list[WebhookEndpoint]:
        """List all webhook endpoints for the current tenant."""
        tenant_id = get_tenant_id()

        result = await db.execute(
            select(WebhookEndpoint)
            .where(WebhookEndpoint.tenant_id == tenant_id)
            .order_by(WebhookEndpoint.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_active_endpoints_for_event(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        event_type: str,
    ) -> list[WebhookEndpoint]:
        """Get all active endpoints subscribed to an event type."""
        result = await db.execute(
            select(WebhookEndpoint).where(
                and_(
                    WebhookEndpoint.tenant_id == tenant_id,
                    WebhookEndpoint.is_active == True,  # noqa: E712
                )
            )
        )
        endpoints = result.scalars().all()

        # Filter by event type (events is a JSONB array)
        return [ep for ep in endpoints if event_type in ep.events]

    async def dispatch_event(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        event_type: str,
        payload: dict[str, Any],
    ) -> list[WebhookEvent]:
        """Dispatch an event to all subscribed endpoints."""
        endpoints = await self.get_active_endpoints_for_event(db, tenant_id, event_type)

        events = []
        for endpoint in endpoints:
            event = await self._create_event(db, endpoint.id, event_type, payload)
            events.append(event)

            # Deliver asynchronously (in production, use a task queue)
            await self._deliver_event(db, event, endpoint)

        return events

    async def _create_event(
        self,
        db: AsyncSession,
        endpoint_id: UUID,
        event_type: str,
        payload: dict[str, Any],
    ) -> WebhookEvent:
        """Create a webhook event record."""
        event = WebhookEvent(
            endpoint_id=endpoint_id,
            event_type=event_type,
            payload=payload,
            status="PENDING",
            attempts=0,
        )

        db.add(event)
        await db.commit()
        await db.refresh(event)

        return event

    async def _deliver_event(
        self,
        db: AsyncSession,
        event: WebhookEvent,
        endpoint: WebhookEndpoint,
    ) -> bool:
        """Deliver a webhook event to its endpoint."""
        body = json.dumps(
            {
                "event": event.event_type,
                "data": event.payload,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "webhook_id": str(event.id),
            }
        )

        signature = self._sign_payload(body, endpoint.secret)

        headers = {
            "Content-Type": "application/json",
            "X-ClassUp-Signature": signature,
            "X-ClassUp-Event": event.event_type,
            "X-ClassUp-Delivery": str(event.id),
        }

        event.attempts += 1
        event.last_attempt_at = datetime.now(timezone.utc)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(endpoint.url, content=body, headers=headers)

                event.response_code = response.status_code
                event.response_body = response.text[:1000] if response.text else None

                if response.status_code >= 200 and response.status_code < 300:
                    event.status = "DELIVERED"
                    logger.info(f"Webhook event {event.id} delivered successfully")
                else:
                    event.status = "FAILED"
                    logger.warning(
                        f"Webhook event {event.id} failed with status {response.status_code}"
                    )

        except Exception as e:
            event.status = "FAILED"
            event.response_body = str(e)[:1000]
            logger.error(f"Webhook event {event.id} delivery error: {e}")

        await db.commit()
        return event.status == "DELIVERED"

    async def test_endpoint(
        self,
        db: AsyncSession,
        endpoint_id: UUID,
        event_type: str,
    ) -> dict:
        """Send a test event to a webhook endpoint."""
        endpoint = await self.get_endpoint(db, endpoint_id)
        if not endpoint:
            return {"success": False, "error": "Endpoint not found"}

        # Create test payload
        test_payload = {
            "test": True,
            "message": "This is a test webhook from ClassUp",
            "event_type": event_type,
        }

        body = json.dumps(
            {
                "event": event_type,
                "data": test_payload,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "test": True,
            }
        )

        signature = self._sign_payload(body, endpoint.secret)

        headers = {
            "Content-Type": "application/json",
            "X-ClassUp-Signature": signature,
            "X-ClassUp-Event": event_type,
            "X-ClassUp-Test": "true",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(endpoint.url, content=body, headers=headers)

                return {
                    "success": response.status_code >= 200 and response.status_code < 300,
                    "status_code": response.status_code,
                    "response_body": response.text[:500] if response.text else None,
                }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
            }

    async def get_endpoint_events(
        self,
        db: AsyncSession,
        endpoint_id: UUID,
        limit: int = 50,
    ) -> list[WebhookEvent]:
        """Get recent events for a webhook endpoint."""
        # Verify endpoint belongs to tenant
        endpoint = await self.get_endpoint(db, endpoint_id)
        if not endpoint:
            return []

        result = await db.execute(
            select(WebhookEvent)
            .where(WebhookEvent.endpoint_id == endpoint_id)
            .order_by(WebhookEvent.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def retry_failed_event(
        self,
        db: AsyncSession,
        event_id: UUID,
    ) -> bool:
        """Retry a failed webhook event."""
        result = await db.execute(select(WebhookEvent).where(WebhookEvent.id == event_id))
        event = result.scalar_one_or_none()

        if not event or event.status != "FAILED":
            return False

        # Get endpoint
        endpoint = await db.get(WebhookEndpoint, event.endpoint_id)
        if not endpoint:
            return False

        # Verify tenant ownership
        tenant_id = get_tenant_id()
        if endpoint.tenant_id != tenant_id:
            return False

        # Reset status and retry
        event.status = "PENDING"
        await db.commit()

        return await self._deliver_event(db, event, endpoint)


# Singleton instance
_webhook_service: WebhookService | None = None


def get_webhook_service() -> WebhookService:
    """Get the webhook service singleton."""
    global _webhook_service
    if _webhook_service is None:
        _webhook_service = WebhookService()
    return _webhook_service
