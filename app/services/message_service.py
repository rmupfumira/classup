"""Message service for managing communication."""

import uuid
from datetime import datetime

from sqlalchemy import func, select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.exceptions import ForbiddenException, NotFoundException
from app.models import (
    Message,
    MessageAttachment,
    MessageRecipient,
    ParentStudent,
    SchoolClass,
    Student,
    TeacherClass,
    User,
)
from app.models.message import MessageStatus, MessageType
from app.models.user import Role
from app.schemas.message import MessageCreate, MessageReply
from app.utils.tenant_context import get_current_user_id, get_current_user_role, get_tenant_id


class MessageService:
    """Service for managing messages and communication."""

    async def get_inbox(
        self,
        db: AsyncSession,
        message_type: str | None = None,
        is_read: bool | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Message], int]:
        """Get inbox messages for the current user."""
        tenant_id = get_tenant_id()
        user_id = get_current_user_id()

        # Get messages where user is a recipient
        query = (
            select(Message)
            .join(MessageRecipient, MessageRecipient.message_id == Message.id)
            .where(
                Message.tenant_id == tenant_id,
                Message.deleted_at.is_(None),
                MessageRecipient.user_id == user_id,
                Message.parent_message_id.is_(None),  # Only top-level messages
            )
            .options(
                selectinload(Message.sender),
                selectinload(Message.school_class),
                selectinload(Message.student),
                selectinload(Message.attachments),
                selectinload(Message.recipients),
            )
        )

        # Apply filters
        if message_type:
            query = query.where(Message.message_type == message_type)
        if is_read is not None:
            query = query.where(MessageRecipient.is_read == is_read)

        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_query)).scalar() or 0

        # Apply pagination and ordering
        query = query.order_by(Message.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(query)
        messages = list(result.scalars().unique().all())

        return messages, total

    async def get_sent_messages(
        self,
        db: AsyncSession,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Message], int]:
        """Get messages sent by the current user."""
        tenant_id = get_tenant_id()
        user_id = get_current_user_id()

        query = (
            select(Message)
            .where(
                Message.tenant_id == tenant_id,
                Message.sender_id == user_id,
                Message.deleted_at.is_(None),
                Message.parent_message_id.is_(None),
            )
            .options(
                selectinload(Message.sender),
                selectinload(Message.school_class),
                selectinload(Message.student),
                selectinload(Message.attachments),
                selectinload(Message.recipients),
            )
        )

        count_query = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_query)).scalar() or 0

        query = query.order_by(Message.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(query)
        messages = list(result.scalars().unique().all())

        return messages, total

    async def get_message(
        self,
        db: AsyncSession,
        message_id: uuid.UUID,
    ) -> Message:
        """Get a single message by ID."""
        tenant_id = get_tenant_id()
        user_id = get_current_user_id()

        query = (
            select(Message)
            .where(
                Message.id == message_id,
                Message.tenant_id == tenant_id,
                Message.deleted_at.is_(None),
            )
            .options(
                selectinload(Message.sender),
                selectinload(Message.school_class),
                selectinload(Message.student),
                selectinload(Message.attachments).selectinload(MessageAttachment.file_entity),
                selectinload(Message.recipients).selectinload(MessageRecipient.user),
            )
        )

        result = await db.execute(query)
        message = result.scalar_one_or_none()

        if not message:
            raise NotFoundException("Message")

        # Check if user can view this message
        if not await self._can_view_message(db, message, user_id):
            raise ForbiddenException("You don't have permission to view this message")

        return message

    async def get_thread(
        self,
        db: AsyncSession,
        message_id: uuid.UUID,
    ) -> tuple[Message, list[Message]]:
        """Get a message and all its replies."""
        tenant_id = get_tenant_id()
        user_id = get_current_user_id()

        # Get the original message
        original = await self.get_message(db, message_id)

        # Get all replies
        replies_query = (
            select(Message)
            .where(
                Message.tenant_id == tenant_id,
                Message.parent_message_id == message_id,
                Message.deleted_at.is_(None),
            )
            .options(
                selectinload(Message.sender),
                selectinload(Message.attachments).selectinload(MessageAttachment.file_entity),
            )
            .order_by(Message.created_at.asc())
        )

        result = await db.execute(replies_query)
        replies = list(result.scalars().unique().all())

        # Mark as read for current user
        await self._mark_as_read(db, message_id, user_id)

        return original, replies

    async def create_message(
        self,
        db: AsyncSession,
        data: MessageCreate,
    ) -> Message:
        """Create a new message and resolve recipients."""
        tenant_id = get_tenant_id()
        user_id = get_current_user_id()
        role = get_current_user_role()

        # Validate permissions based on message type
        await self._validate_message_permissions(data.message_type, role)

        # Validate referenced entities
        if data.class_id:
            await self._validate_class(db, data.class_id)
        if data.student_id:
            await self._validate_student(db, data.student_id)

        # Create the message
        message = Message(
            tenant_id=tenant_id,
            sender_id=user_id,
            message_type=data.message_type.value,
            subject=data.subject,
            body=data.body,
            class_id=data.class_id,
            student_id=data.student_id,
            parent_message_id=data.parent_message_id,
            status=MessageStatus.SENT.value,
        )

        db.add(message)
        await db.flush()

        # Resolve and create recipients
        recipient_ids = await self._resolve_recipients(
            db, data.message_type, data.class_id, data.student_id, data.parent_message_id
        )

        for recipient_id in recipient_ids:
            if recipient_id != user_id:  # Don't add sender as recipient
                recipient = MessageRecipient(
                    message_id=message.id,
                    user_id=recipient_id,
                    is_read=False,
                )
                db.add(recipient)

        # Add attachments
        for i, file_id in enumerate(data.attachment_ids):
            attachment = MessageAttachment(
                message_id=message.id,
                file_entity_id=file_id,
                display_order=i,
            )
            db.add(attachment)

        await db.flush()
        await db.refresh(message)

        return message

    async def reply_to_message(
        self,
        db: AsyncSession,
        message_id: uuid.UUID,
        data: MessageReply,
    ) -> Message:
        """Reply to an existing message."""
        tenant_id = get_tenant_id()
        user_id = get_current_user_id()

        # Get the original message
        original = await self.get_message(db, message_id)

        # Create the reply
        reply = Message(
            tenant_id=tenant_id,
            sender_id=user_id,
            message_type=MessageType.REPLY.value,
            subject=f"Re: {original.subject}" if original.subject else None,
            body=data.body,
            class_id=original.class_id,
            student_id=original.student_id,
            parent_message_id=message_id,
            status=MessageStatus.SENT.value,
        )

        db.add(reply)
        await db.flush()

        # Add all thread participants as recipients
        recipient_ids = await self._get_thread_participants(db, message_id)
        for recipient_id in recipient_ids:
            if recipient_id != user_id:
                recipient = MessageRecipient(
                    message_id=reply.id,
                    user_id=recipient_id,
                    is_read=False,
                )
                db.add(recipient)

        # Add attachments
        for i, file_id in enumerate(data.attachment_ids):
            attachment = MessageAttachment(
                message_id=reply.id,
                file_entity_id=file_id,
                display_order=i,
            )
            db.add(attachment)

        await db.flush()
        await db.refresh(reply)

        return reply

    async def mark_as_read(
        self,
        db: AsyncSession,
        message_ids: list[uuid.UUID],
    ) -> int:
        """Mark messages as read for the current user."""
        user_id = get_current_user_id()
        count = 0

        for message_id in message_ids:
            result = await self._mark_as_read(db, message_id, user_id)
            if result:
                count += 1

        return count

    async def get_unread_count(
        self,
        db: AsyncSession,
    ) -> dict:
        """Get unread message counts for the current user."""
        tenant_id = get_tenant_id()
        user_id = get_current_user_id()

        # Total unread
        total_query = (
            select(func.count())
            .select_from(MessageRecipient)
            .join(Message, Message.id == MessageRecipient.message_id)
            .where(
                Message.tenant_id == tenant_id,
                Message.deleted_at.is_(None),
                MessageRecipient.user_id == user_id,
                MessageRecipient.is_read == False,
            )
        )
        total_result = await db.execute(total_query)
        total = total_result.scalar() or 0

        # Announcement unread
        announcement_query = (
            select(func.count())
            .select_from(MessageRecipient)
            .join(Message, Message.id == MessageRecipient.message_id)
            .where(
                Message.tenant_id == tenant_id,
                Message.deleted_at.is_(None),
                Message.message_type.in_([
                    MessageType.ANNOUNCEMENT.value,
                    MessageType.CLASS_ANNOUNCEMENT.value,
                ]),
                MessageRecipient.user_id == user_id,
                MessageRecipient.is_read == False,
            )
        )
        announcement_result = await db.execute(announcement_query)
        announcements = announcement_result.scalar() or 0

        return {
            "messages": total,
            "announcements": announcements,
        }

    async def get_announcements(
        self,
        db: AsyncSession,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Message], int]:
        """Get announcement messages."""
        tenant_id = get_tenant_id()
        user_id = get_current_user_id()

        query = (
            select(Message)
            .join(MessageRecipient, MessageRecipient.message_id == Message.id)
            .where(
                Message.tenant_id == tenant_id,
                Message.deleted_at.is_(None),
                Message.message_type.in_([
                    MessageType.ANNOUNCEMENT.value,
                    MessageType.CLASS_ANNOUNCEMENT.value,
                ]),
                MessageRecipient.user_id == user_id,
            )
            .options(
                selectinload(Message.sender),
                selectinload(Message.school_class),
                selectinload(Message.attachments),
            )
        )

        count_query = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_query)).scalar() or 0

        query = query.order_by(Message.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(query)
        messages = list(result.scalars().unique().all())

        return messages, total

    async def _resolve_recipients(
        self,
        db: AsyncSession,
        message_type: MessageType,
        class_id: uuid.UUID | None,
        student_id: uuid.UUID | None,
        parent_message_id: uuid.UUID | None,
    ) -> list[uuid.UUID]:
        """Resolve recipients based on message type."""
        tenant_id = get_tenant_id()
        recipients = set()

        if message_type == MessageType.ANNOUNCEMENT:
            # All parents in the tenant
            query = select(User.id).where(
                User.tenant_id == tenant_id,
                User.role == Role.PARENT,
                User.deleted_at.is_(None),
                User.is_active == True,
            )
            result = await db.execute(query)
            recipients.update(r[0] for r in result.all())

        elif message_type == MessageType.CLASS_ANNOUNCEMENT:
            # All parents with children in the class
            if class_id:
                query = (
                    select(User.id)
                    .join(ParentStudent, ParentStudent.parent_id == User.id)
                    .join(Student, Student.id == ParentStudent.student_id)
                    .where(
                        Student.class_id == class_id,
                        User.deleted_at.is_(None),
                    )
                )
                result = await db.execute(query)
                recipients.update(r[0] for r in result.all())

        elif message_type in (MessageType.STUDENT_MESSAGE, MessageType.STUDENT_PHOTO, MessageType.STUDENT_DOCUMENT):
            # Parents of the specific student
            if student_id:
                query = (
                    select(User.id)
                    .join(ParentStudent, ParentStudent.parent_id == User.id)
                    .where(
                        ParentStudent.student_id == student_id,
                        User.deleted_at.is_(None),
                    )
                )
                result = await db.execute(query)
                recipients.update(r[0] for r in result.all())

        elif message_type in (MessageType.CLASS_PHOTO, MessageType.CLASS_DOCUMENT):
            # All parents with children in the class (same as CLASS_ANNOUNCEMENT)
            if class_id:
                query = (
                    select(User.id)
                    .join(ParentStudent, ParentStudent.parent_id == User.id)
                    .join(Student, Student.id == ParentStudent.student_id)
                    .where(
                        Student.class_id == class_id,
                        User.deleted_at.is_(None),
                    )
                )
                result = await db.execute(query)
                recipients.update(r[0] for r in result.all())

        elif message_type == MessageType.SCHOOL_DOCUMENT:
            # All parents in the tenant (same as ANNOUNCEMENT)
            query = select(User.id).where(
                User.tenant_id == tenant_id,
                User.role == Role.PARENT,
                User.deleted_at.is_(None),
                User.is_active == True,
            )
            result = await db.execute(query)
            recipients.update(r[0] for r in result.all())

        elif message_type == MessageType.REPLY:
            # Thread participants (handled separately)
            if parent_message_id:
                recipients.update(await self._get_thread_participants(db, parent_message_id))

        return list(recipients)

    async def _get_thread_participants(
        self,
        db: AsyncSession,
        message_id: uuid.UUID,
    ) -> list[uuid.UUID]:
        """Get all participants in a message thread."""
        participants = set()

        # Get original message sender
        message = await self.get_message(db, message_id)
        participants.add(message.sender_id)

        # Get all recipients
        for recipient in message.recipients:
            participants.add(recipient.user_id)

        # Get all reply senders
        reply_query = select(Message.sender_id).where(
            Message.parent_message_id == message_id,
            Message.deleted_at.is_(None),
        )
        result = await db.execute(reply_query)
        for r in result.all():
            participants.add(r[0])

        return list(participants)

    async def _mark_as_read(
        self,
        db: AsyncSession,
        message_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> bool:
        """Mark a message as read for a user."""
        query = select(MessageRecipient).where(
            MessageRecipient.message_id == message_id,
            MessageRecipient.user_id == user_id,
            MessageRecipient.is_read == False,
        )
        result = await db.execute(query)
        recipient = result.scalar_one_or_none()

        if recipient:
            recipient.is_read = True
            recipient.read_at = datetime.utcnow()
            return True

        return False

    async def _can_view_message(
        self,
        db: AsyncSession,
        message: Message,
        user_id: uuid.UUID,
    ) -> bool:
        """Check if a user can view a message."""
        # Sender can always view
        if message.sender_id == user_id:
            return True

        # Check if user is a recipient
        for recipient in message.recipients:
            if recipient.user_id == user_id:
                return True

        # Check user role - admins can view all messages in their tenant
        role = get_current_user_role()
        if role in (Role.SUPER_ADMIN.value, Role.SCHOOL_ADMIN.value):
            return True

        return False

    async def _validate_message_permissions(
        self,
        message_type: MessageType,
        role: str,
    ) -> None:
        """Validate that the user role can send this message type."""
        if message_type in (MessageType.ANNOUNCEMENT, MessageType.SCHOOL_DOCUMENT):
            if role not in (Role.SUPER_ADMIN.value, Role.SCHOOL_ADMIN.value):
                raise ForbiddenException("Only administrators can send school-wide announcements")

        elif message_type in (
            MessageType.CLASS_ANNOUNCEMENT,
            MessageType.STUDENT_MESSAGE,
            MessageType.CLASS_PHOTO,
            MessageType.STUDENT_PHOTO,
            MessageType.CLASS_DOCUMENT,
            MessageType.STUDENT_DOCUMENT,
        ):
            if role not in (Role.SUPER_ADMIN.value, Role.SCHOOL_ADMIN.value, Role.TEACHER.value):
                raise ForbiddenException("Only staff can send this type of message")

    async def _validate_class(self, db: AsyncSession, class_id: uuid.UUID) -> None:
        """Validate that a class exists."""
        tenant_id = get_tenant_id()
        query = select(SchoolClass).where(
            SchoolClass.id == class_id,
            SchoolClass.tenant_id == tenant_id,
            SchoolClass.deleted_at.is_(None),
        )
        result = await db.execute(query)
        if not result.scalar_one_or_none():
            raise NotFoundException("Class")

    async def _validate_student(self, db: AsyncSession, student_id: uuid.UUID) -> None:
        """Validate that a student exists."""
        tenant_id = get_tenant_id()
        query = select(Student).where(
            Student.id == student_id,
            Student.tenant_id == tenant_id,
            Student.deleted_at.is_(None),
        )
        result = await db.execute(query)
        if not result.scalar_one_or_none():
            raise NotFoundException("Student")


def get_message_service() -> MessageService:
    """Get message service instance."""
    return MessageService()
