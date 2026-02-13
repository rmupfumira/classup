"""Webhook API endpoints."""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.schemas.common import APIResponse
from app.schemas.webhook import (
    WebhookEndpointCreate,
    WebhookEndpointResponse,
    WebhookEndpointUpdate,
    WebhookEventResponse,
    WebhookTestRequest,
    WebhookTestResponse,
)
from app.services.webhook_service import get_webhook_service
from app.utils.permissions import require_role

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("", response_model=APIResponse)
@require_role("SCHOOL_ADMIN")
async def list_endpoints(
    db: AsyncSession = Depends(get_db_session),
):
    """List all webhook endpoints."""
    service = get_webhook_service()

    endpoints = await service.list_endpoints(db)

    return APIResponse(
        status="success",
        data=[
            WebhookEndpointResponse(
                id=ep.id,
                tenant_id=ep.tenant_id,
                url=ep.url,
                events=ep.events,
                is_active=ep.is_active,
                created_at=ep.created_at,
                updated_at=ep.updated_at,
            )
            for ep in endpoints
        ],
    )


@router.post("", response_model=APIResponse)
@require_role("SCHOOL_ADMIN")
async def create_endpoint(
    data: WebhookEndpointCreate,
    db: AsyncSession = Depends(get_db_session),
):
    """Create a new webhook endpoint."""
    service = get_webhook_service()

    endpoint = await service.create_endpoint(
        db,
        url=str(data.url),
        events=[e.value for e in data.events],
        is_active=data.is_active,
    )

    return APIResponse(
        status="success",
        data=WebhookEndpointResponse(
            id=endpoint.id,
            tenant_id=endpoint.tenant_id,
            url=endpoint.url,
            events=endpoint.events,
            is_active=endpoint.is_active,
            created_at=endpoint.created_at,
            updated_at=endpoint.updated_at,
        ),
        message="Webhook endpoint created",
    )


@router.get("/{endpoint_id}", response_model=APIResponse)
@require_role("SCHOOL_ADMIN")
async def get_endpoint(
    endpoint_id: UUID,
    db: AsyncSession = Depends(get_db_session),
):
    """Get a webhook endpoint."""
    service = get_webhook_service()

    endpoint = await service.get_endpoint(db, endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")

    return APIResponse(
        status="success",
        data=WebhookEndpointResponse(
            id=endpoint.id,
            tenant_id=endpoint.tenant_id,
            url=endpoint.url,
            events=endpoint.events,
            is_active=endpoint.is_active,
            created_at=endpoint.created_at,
            updated_at=endpoint.updated_at,
        ),
    )


@router.put("/{endpoint_id}", response_model=APIResponse)
@require_role("SCHOOL_ADMIN")
async def update_endpoint(
    endpoint_id: UUID,
    data: WebhookEndpointUpdate,
    db: AsyncSession = Depends(get_db_session),
):
    """Update a webhook endpoint."""
    service = get_webhook_service()

    endpoint = await service.update_endpoint(
        db,
        endpoint_id,
        url=str(data.url) if data.url else None,
        events=[e.value for e in data.events] if data.events else None,
        is_active=data.is_active,
    )

    if not endpoint:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")

    return APIResponse(
        status="success",
        data=WebhookEndpointResponse(
            id=endpoint.id,
            tenant_id=endpoint.tenant_id,
            url=endpoint.url,
            events=endpoint.events,
            is_active=endpoint.is_active,
            created_at=endpoint.created_at,
            updated_at=endpoint.updated_at,
        ),
        message="Webhook endpoint updated",
    )


@router.delete("/{endpoint_id}", response_model=APIResponse)
@require_role("SCHOOL_ADMIN")
async def delete_endpoint(
    endpoint_id: UUID,
    db: AsyncSession = Depends(get_db_session),
):
    """Delete a webhook endpoint."""
    service = get_webhook_service()

    success = await service.delete_endpoint(db, endpoint_id)
    if not success:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")

    return APIResponse(
        status="success",
        message="Webhook endpoint deleted",
    )


@router.get("/{endpoint_id}/events", response_model=APIResponse)
@require_role("SCHOOL_ADMIN")
async def get_endpoint_events(
    endpoint_id: UUID,
    limit: int = 50,
    db: AsyncSession = Depends(get_db_session),
):
    """Get recent events for a webhook endpoint."""
    service = get_webhook_service()

    events = await service.get_endpoint_events(db, endpoint_id, limit=limit)

    return APIResponse(
        status="success",
        data=[
            WebhookEventResponse(
                id=ev.id,
                endpoint_id=ev.endpoint_id,
                event_type=ev.event_type,
                payload=ev.payload,
                status=ev.status,
                attempts=ev.attempts,
                last_attempt_at=ev.last_attempt_at,
                response_code=ev.response_code,
                response_body=ev.response_body,
                created_at=ev.created_at,
            )
            for ev in events
        ],
    )


@router.post("/{endpoint_id}/test", response_model=APIResponse)
@require_role("SCHOOL_ADMIN")
async def test_endpoint(
    endpoint_id: UUID,
    data: WebhookTestRequest,
    db: AsyncSession = Depends(get_db_session),
):
    """Send a test event to a webhook endpoint."""
    service = get_webhook_service()

    result = await service.test_endpoint(db, endpoint_id, data.event_type.value)

    return APIResponse(
        status="success",
        data=WebhookTestResponse(**result),
        message="Test webhook sent" if result.get("success") else "Test webhook failed",
    )


@router.post("/{endpoint_id}/events/{event_id}/retry", response_model=APIResponse)
@require_role("SCHOOL_ADMIN")
async def retry_event(
    endpoint_id: UUID,
    event_id: UUID,
    db: AsyncSession = Depends(get_db_session),
):
    """Retry a failed webhook event."""
    service = get_webhook_service()

    success = await service.retry_failed_event(db, event_id)

    if not success:
        raise HTTPException(status_code=400, detail="Cannot retry this event")

    return APIResponse(
        status="success",
        message="Event retry initiated",
    )
