"""Message API endpoints."""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import Role
from app.schemas.common import APIResponse, PaginationMeta
from app.schemas.message import (
    AnnouncementCreate,
    AttachmentInfo,
    MarkReadRequest,
    MessageCreate,
    MessageListResponse,
    MessageReply,
    MessageResponse,
    MessageThreadResponse,
    RecipientInfo,
    StudentMessageCreate,
    UnreadCountResponse,
)
from app.services.message_service import get_message_service
from app.utils.permissions import require_role


router = APIRouter()


def _build_message_response(message) -> MessageResponse:
    """Build message response with related data."""
    # Check if current user has read this message
    is_read = False
    for recipient in message.recipients:
        if recipient.is_read:
            is_read = True
            break

    return MessageResponse(
        id=message.id,
        tenant_id=message.tenant_id,
        sender_id=message.sender_id,
        message_type=message.message_type,
        subject=message.subject,
        body=message.body,
        class_id=message.class_id,
        student_id=message.student_id,
        parent_message_id=message.parent_message_id,
        status=message.status,
        created_at=message.created_at,
        updated_at=message.updated_at,
        sender_name=f"{message.sender.first_name} {message.sender.last_name}" if message.sender else None,
        sender_avatar=message.sender.avatar_path if message.sender else None,
        class_name=message.school_class.name if message.school_class else None,
        student_name=f"{message.student.first_name} {message.student.last_name}" if message.student else None,
        is_read=is_read,
        attachments=[
            AttachmentInfo(
                id=att.id,
                file_entity_id=att.file_entity_id,
                original_name=att.file_entity.original_name if att.file_entity else "",
                content_type=att.file_entity.content_type if att.file_entity else "",
                file_size=att.file_entity.file_size if att.file_entity else 0,
                display_order=att.display_order,
            )
            for att in message.attachments
        ] if message.attachments else [],
        reply_count=0,  # TODO: Calculate reply count
    )


def _build_message_list_response(message) -> MessageListResponse:
    """Build message list response (simplified view)."""
    # Check if current user has read this message
    is_read = False
    for recipient in message.recipients:
        if recipient.is_read:
            is_read = True
            break

    # Truncate body for preview
    body_preview = message.body[:100] + "..." if len(message.body) > 100 else message.body

    return MessageListResponse(
        id=message.id,
        sender_id=message.sender_id,
        sender_name=f"{message.sender.first_name} {message.sender.last_name}" if message.sender else "Unknown",
        sender_avatar=message.sender.avatar_path if message.sender else None,
        message_type=message.message_type,
        subject=message.subject,
        body_preview=body_preview,
        class_name=message.school_class.name if message.school_class else None,
        student_name=f"{message.student.first_name} {message.student.last_name}" if message.student else None,
        is_read=is_read,
        has_attachments=bool(message.attachments),
        created_at=message.created_at,
    )


@router.get("", response_model=APIResponse[list[MessageListResponse]])
async def list_messages(
    message_type: str | None = Query(None, description="Filter by message type"),
    is_read: bool | None = Query(None, description="Filter by read status"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
):
    """List inbox messages for the current user.

    Returns paginated list of messages where the user is a recipient.
    """
    service = get_message_service()
    messages, total = await service.get_inbox(
        db,
        message_type=message_type,
        is_read=is_read,
        page=page,
        page_size=page_size,
    )

    total_pages = (total + page_size - 1) // page_size

    return APIResponse(
        data=[_build_message_list_response(m) for m in messages],
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
            total_items=total,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_prev=page > 1,
        ),
    )


@router.get("/sent", response_model=APIResponse[list[MessageListResponse]])
async def list_sent_messages(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
):
    """List messages sent by the current user."""
    service = get_message_service()
    messages, total = await service.get_sent_messages(db, page=page, page_size=page_size)

    total_pages = (total + page_size - 1) // page_size

    return APIResponse(
        data=[_build_message_list_response(m) for m in messages],
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
            total_items=total,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_prev=page > 1,
        ),
    )


@router.get("/announcements", response_model=APIResponse[list[MessageListResponse]])
async def list_announcements(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
):
    """List announcement messages for the current user."""
    service = get_message_service()
    messages, total = await service.get_announcements(db, page=page, page_size=page_size)

    total_pages = (total + page_size - 1) // page_size

    return APIResponse(
        data=[_build_message_list_response(m) for m in messages],
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
async def get_unread_count(
    db: AsyncSession = Depends(get_db),
):
    """Get unread message counts for the current user."""
    service = get_message_service()
    counts = await service.get_unread_count(db)

    return APIResponse(
        data=UnreadCountResponse(
            messages=counts["messages"],
            announcements=counts["announcements"],
        )
    )


@router.post("", response_model=APIResponse[MessageResponse])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def create_message(
    data: MessageCreate,
    db: AsyncSession = Depends(get_db),
):
    """Send a new message.

    Recipients are automatically resolved based on message type:
    - ANNOUNCEMENT: All parents in the tenant
    - CLASS_ANNOUNCEMENT: All parents with children in the class
    - STUDENT_MESSAGE: Parents of the specific student
    """
    service = get_message_service()
    message = await service.create_message(db, data)
    await db.commit()
    await db.refresh(message)

    return APIResponse(
        data=_build_message_response(message),
        message="Message sent successfully",
    )


@router.post("/announcement", response_model=APIResponse[MessageResponse])
@require_role(Role.SCHOOL_ADMIN)
async def create_announcement(
    data: AnnouncementCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a school-wide or class announcement.

    Convenience endpoint for creating announcements.
    """
    from app.schemas.message import MessageType

    message_data = MessageCreate(
        message_type=MessageType.CLASS_ANNOUNCEMENT if data.class_id else MessageType.ANNOUNCEMENT,
        subject=data.subject,
        body=data.body,
        class_id=data.class_id,
    )

    service = get_message_service()
    message = await service.create_message(db, message_data)
    await db.commit()
    await db.refresh(message)

    return APIResponse(
        data=_build_message_response(message),
        message="Announcement sent successfully",
    )


@router.post("/student-message", response_model=APIResponse[MessageResponse])
@require_role(Role.SCHOOL_ADMIN, Role.TEACHER)
async def create_student_message(
    data: StudentMessageCreate,
    db: AsyncSession = Depends(get_db),
):
    """Send a message about a specific student to their parents.

    Convenience endpoint for student-specific messages.
    """
    from app.schemas.message import MessageType

    message_data = MessageCreate(
        message_type=MessageType.STUDENT_MESSAGE,
        subject=data.subject,
        body=data.body,
        student_id=data.student_id,
        attachment_ids=data.attachment_ids,
    )

    service = get_message_service()
    message = await service.create_message(db, message_data)
    await db.commit()
    await db.refresh(message)

    return APIResponse(
        data=_build_message_response(message),
        message="Message sent successfully",
    )


@router.get("/{message_id}", response_model=APIResponse[MessageResponse])
async def get_message(
    message_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get a single message by ID."""
    service = get_message_service()
    message = await service.get_message(db, message_id)

    return APIResponse(data=_build_message_response(message))


@router.get("/{message_id}/thread", response_model=APIResponse[MessageThreadResponse])
async def get_message_thread(
    message_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get a message and all its replies.

    Also marks the message as read for the current user.
    """
    service = get_message_service()
    original, replies = await service.get_thread(db, message_id)
    await db.commit()

    # Get participants
    participants = [
        RecipientInfo(
            user_id=r.user_id,
            first_name=r.user.first_name if r.user else "",
            last_name=r.user.last_name if r.user else "",
            is_read=r.is_read,
            read_at=r.read_at,
        )
        for r in original.recipients
    ]

    return APIResponse(
        data=MessageThreadResponse(
            original_message=_build_message_response(original),
            replies=[_build_message_response(r) for r in replies],
            participants=participants,
        )
    )


@router.post("/{message_id}/reply", response_model=APIResponse[MessageResponse])
async def reply_to_message(
    message_id: uuid.UUID,
    data: MessageReply,
    db: AsyncSession = Depends(get_db),
):
    """Reply to a message.

    All thread participants will receive the reply.
    """
    service = get_message_service()
    reply = await service.reply_to_message(db, message_id, data)
    await db.commit()
    await db.refresh(reply)

    return APIResponse(
        data=_build_message_response(reply),
        message="Reply sent successfully",
    )


@router.put("/{message_id}/read", response_model=APIResponse[dict])
async def mark_message_as_read(
    message_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Mark a message as read."""
    service = get_message_service()
    count = await service.mark_as_read(db, [message_id])
    await db.commit()

    return APIResponse(
        data={"marked_count": count},
        message="Message marked as read",
    )


@router.put("/read", response_model=APIResponse[dict])
async def mark_messages_as_read(
    data: MarkReadRequest,
    db: AsyncSession = Depends(get_db),
):
    """Mark multiple messages as read."""
    service = get_message_service()
    count = await service.mark_as_read(db, data.message_ids)
    await db.commit()

    return APIResponse(
        data={"marked_count": count},
        message=f"{count} message(s) marked as read",
    )
