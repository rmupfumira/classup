"""Message API endpoints for teacher-parent conversations."""

import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import Role
from app.schemas.common import APIResponse, PaginationMeta
from app.schemas.message import (
    ConversationSummary,
    MessageCreate,
    MessageReply,
    MessageResponse,
    UnreadCountResponse,
)
from app.services.message_service import get_message_service
from app.utils.permissions import require_role
from app.utils.tenant_context import get_current_user_id

router = APIRouter()


def _build_message_response(message) -> MessageResponse:
    """Build a message response from a Message model."""
    return MessageResponse(
        id=message.id,
        sender_id=message.sender_id,
        sender_name=(
            f"{message.sender.first_name} {message.sender.last_name}"
            if message.sender else None
        ),
        sender_role=message.sender.role if message.sender else None,
        message_type=message.message_type,
        subject=message.subject,
        body=message.body,
        student_id=message.student_id,
        student_name=(
            f"{message.student.first_name} {message.student.last_name}"
            if message.student else None
        ),
        class_name=message.school_class.name if message.school_class else None,
        parent_message_id=message.parent_message_id,
        status=message.status,
        is_read=any(
            r.is_read for r in message.recipients
            if r.user_id == get_current_user_id()
        ) if message.recipients else True,
        created_at=message.created_at,
        updated_at=message.updated_at,
    )


@router.get("", response_model=APIResponse[list[ConversationSummary]])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER, Role.PARENT)
async def list_conversations(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List conversations (inbox)."""
    service = get_message_service()
    conversations, total = await service.get_conversations(db, page=page, page_size=page_size)

    total_pages = (total + page_size - 1) // page_size if total > 0 else 0

    return APIResponse(
        data=[ConversationSummary(**c) for c in conversations],
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
            total_items=total,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_prev=page > 1,
        ),
    )


@router.get("/unread-count", response_model=APIResponse[UnreadCountResponse])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER, Role.PARENT)
async def get_unread_count(
    db: AsyncSession = Depends(get_db),
):
    """Get unread message count."""
    service = get_message_service()
    count = await service.get_unread_count(db)
    return APIResponse(data=UnreadCountResponse(count=count))


@router.get("/compose-context", response_model=APIResponse[list])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER, Role.PARENT)
async def get_compose_context(
    db: AsyncSession = Depends(get_db),
):
    """Get students and recipients for compose form."""
    service = get_message_service()
    context = await service.get_compose_context(db)
    return APIResponse(data=context)


@router.get("/thread/{student_id}/{other_user_id}", response_model=APIResponse[list[MessageResponse]])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER, Role.PARENT)
async def get_thread(
    student_id: uuid.UUID,
    other_user_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Get conversation messages (thread)."""
    service = get_message_service()
    messages, total = await service.get_conversation_messages(
        db, student_id, other_user_id, page=page, page_size=page_size,
    )

    total_pages = (total + page_size - 1) // page_size if total > 0 else 0

    return APIResponse(
        data=[_build_message_response(m) for m in messages],
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
            total_items=total,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_prev=page > 1,
        ),
    )


@router.post("", response_model=APIResponse[MessageResponse])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER, Role.PARENT)
async def send_message(
    data: MessageCreate,
    db: AsyncSession = Depends(get_db),
):
    """Send a new message."""
    service = get_message_service()
    message = await service.send_message(db, data.model_dump())
    return APIResponse(
        data=_build_message_response(message),
        message="Message sent",
    )


@router.post("/thread/{student_id}/{other_user_id}/reply", response_model=APIResponse[MessageResponse])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER, Role.PARENT)
async def reply_to_thread(
    student_id: uuid.UUID,
    other_user_id: uuid.UUID,
    data: MessageReply,
    db: AsyncSession = Depends(get_db),
):
    """Reply to a conversation."""
    service = get_message_service()
    message = await service.reply_to_conversation(db, student_id, other_user_id, data.body)
    return APIResponse(
        data=_build_message_response(message),
        message="Reply sent",
    )


@router.put("/thread/{student_id}/{other_user_id}/read", response_model=APIResponse)
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER, Role.PARENT)
async def mark_thread_read(
    student_id: uuid.UUID,
    other_user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Mark conversation as read."""
    service = get_message_service()
    count = await service.mark_conversation_read(db, student_id, other_user_id)
    return APIResponse(message=f"Marked {count} messages as read")
