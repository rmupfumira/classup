"""Message service for teacher-parent conversations scoped to students."""

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.message import Message, MessageRecipient, MessageType
from app.models.school_class import SchoolClass, TeacherClass
from app.models.student import ParentStudent, Student
from app.models.user import User
from app.utils.tenant_context import get_current_user_id, get_current_user_role, get_tenant_id

logger = logging.getLogger(__name__)


class MessageService:
    """Service for student-scoped teacher-parent messaging."""

    async def send_message(
        self,
        db: AsyncSession,
        data: dict,
    ) -> Message:
        """Send a new message to start or continue a conversation."""
        tenant_id = get_tenant_id()
        sender_id = get_current_user_id()
        role = get_current_user_role()

        student_id = data["student_id"]
        recipient_id = data["recipient_id"]
        body = data["body"]
        subject = data.get("subject")

        await self._validate_can_message(db, sender_id, role, student_id, recipient_id)

        # Get student's class_id
        student = await self._get_student(db, student_id)
        class_id = student.class_id

        # Find existing conversation root to thread against
        parent_message_id = await self._find_conversation_root(
            db, tenant_id, student_id, sender_id, recipient_id,
        )

        message = Message(
            tenant_id=tenant_id,
            sender_id=sender_id,
            message_type=MessageType.STUDENT_MESSAGE.value,
            subject=subject,
            body=body,
            student_id=student_id,
            class_id=class_id,
            parent_message_id=parent_message_id,
            status="SENT",
        )
        db.add(message)
        await db.flush()

        recipient = MessageRecipient(
            message_id=message.id,
            user_id=recipient_id,
        )
        db.add(recipient)
        await db.commit()
        await db.refresh(message)

        # Notifications (don't fail the send)
        try:
            await self._send_message_notifications(db, message, recipient_id)
        except Exception as e:
            logger.error(f"Failed to send message notifications: {e}")

        return message

    async def reply_to_conversation(
        self,
        db: AsyncSession,
        student_id: uuid.UUID,
        other_user_id: uuid.UUID,
        body: str,
    ) -> Message:
        """Reply to an existing conversation."""
        tenant_id = get_tenant_id()
        sender_id = get_current_user_id()
        role = get_current_user_role()

        await self._validate_can_message(db, sender_id, role, student_id, other_user_id)

        student = await self._get_student(db, student_id)

        # Find conversation root
        parent_message_id = await self._find_conversation_root(
            db, tenant_id, student_id, sender_id, other_user_id,
        )

        message = Message(
            tenant_id=tenant_id,
            sender_id=sender_id,
            message_type=MessageType.REPLY.value if parent_message_id else MessageType.STUDENT_MESSAGE.value,
            body=body,
            student_id=student_id,
            class_id=student.class_id,
            parent_message_id=parent_message_id,
            status="SENT",
        )
        db.add(message)
        await db.flush()

        recipient = MessageRecipient(
            message_id=message.id,
            user_id=other_user_id,
        )
        db.add(recipient)
        await db.commit()
        await db.refresh(message)

        try:
            await self._send_message_notifications(db, message, other_user_id)
        except Exception as e:
            logger.error(f"Failed to send reply notifications: {e}")

        return message

    async def get_conversations(
        self,
        db: AsyncSession,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict], int]:
        """Get inbox conversations for the current user.

        Returns a list of ConversationSummary-style dicts grouped by (student_id, other_user_id).
        """
        tenant_id = get_tenant_id()
        user_id = get_current_user_id()
        role = get_current_user_role()

        # Get all messages involving the current user (as sender or recipient)
        # in this tenant, about a student
        recipient_msg_ids = (
            select(MessageRecipient.message_id)
            .where(MessageRecipient.user_id == user_id)
            .subquery()
        )

        base_filter = and_(
            Message.tenant_id == tenant_id,
            Message.deleted_at.is_(None),
            Message.student_id.isnot(None),
            or_(
                Message.sender_id == user_id,
                Message.id.in_(select(recipient_msg_ids.c.message_id)),
            ),
        )

        # Build "other_user_id" as a CASE expression:
        # if sender is current user, other is the recipient; else other is the sender
        other_user_id_expr = case(
            (Message.sender_id == user_id, MessageRecipient.user_id),
            else_=Message.sender_id,
        )

        # Join messages with recipients to determine other_user
        conv_query = (
            select(
                Message.student_id,
                other_user_id_expr.label("other_user_id"),
                func.max(Message.created_at).label("last_message_at"),
            )
            .outerjoin(MessageRecipient, MessageRecipient.message_id == Message.id)
            .where(base_filter)
            # Ensure we don't get NULL other_user (self-sent with no recipient match)
            .where(
                or_(
                    and_(Message.sender_id == user_id, MessageRecipient.user_id.isnot(None)),
                    Message.sender_id != user_id,
                )
            )
            .group_by(Message.student_id, "other_user_id")
        )

        # Count total conversations
        count_q = select(func.count()).select_from(conv_query.subquery())
        total = (await db.execute(count_q)).scalar() or 0

        # Get paginated conversation keys
        conv_query = conv_query.order_by(func.max(Message.created_at).desc())
        conv_query = conv_query.offset((page - 1) * page_size).limit(page_size)
        conv_rows = (await db.execute(conv_query)).all()

        if not conv_rows:
            return [], total

        # Batch-fetch all students and users needed (2 queries instead of 2N)
        student_ids = list({row.student_id for row in conv_rows})
        other_user_ids = list({row.other_user_id for row in conv_rows})

        students_result = await db.execute(
            select(Student).where(Student.id.in_(student_ids))
        )
        students_map = {s.id: s for s in students_result.scalars().all()}

        users_result = await db.execute(
            select(User).where(User.id.in_(other_user_ids))
        )
        users_map = {u.id: u for u in users_result.scalars().all()}

        # Batch-fetch unread counts per (student_id, sender_id) — 1 query
        unread_q = (
            select(
                Message.student_id,
                Message.sender_id,
                func.count(MessageRecipient.id).label("cnt"),
            )
            .join(Message, Message.id == MessageRecipient.message_id)
            .where(
                MessageRecipient.user_id == user_id,
                MessageRecipient.is_read == False,
                Message.tenant_id == tenant_id,
                Message.deleted_at.is_(None),
                Message.student_id.in_(student_ids),
            )
            .group_by(Message.student_id, Message.sender_id)
        )
        unread_rows = (await db.execute(unread_q)).all()
        unread_map = {(r.student_id, r.sender_id): r.cnt for r in unread_rows}

        # Batch-fetch last message per conversation using DISTINCT ON equivalent
        # Build OR conditions for each conversation pair
        conv_conditions = []
        for row in conv_rows:
            conv_conditions.append(
                and_(
                    Message.student_id == row.student_id,
                    or_(
                        and_(Message.sender_id == user_id, MessageRecipient.user_id == row.other_user_id),
                        and_(Message.sender_id == row.other_user_id, MessageRecipient.user_id == user_id),
                    ),
                )
            )

        last_msgs_q = (
            select(Message)
            .outerjoin(MessageRecipient, MessageRecipient.message_id == Message.id)
            .where(
                Message.tenant_id == tenant_id,
                Message.deleted_at.is_(None),
                or_(*conv_conditions),
            )
            .order_by(Message.student_id, Message.created_at.desc())
        )
        last_msgs_result = await db.execute(last_msgs_q)
        all_msgs = last_msgs_result.scalars().unique().all()

        # Group by (student_id, other_user) and pick the latest
        last_msg_map: dict[tuple, Message] = {}
        for msg in all_msgs:
            # Determine other_user for this message
            if msg.sender_id == user_id:
                for r in msg.recipients:
                    key = (msg.student_id, r.user_id)
                    if key not in last_msg_map:
                        last_msg_map[key] = msg
            else:
                key = (msg.student_id, msg.sender_id)
                if key not in last_msg_map:
                    last_msg_map[key] = msg

        # Build response
        conversations = []
        for row in conv_rows:
            s_id = row.student_id
            o_id = row.other_user_id

            student = students_map.get(s_id)
            other_user = users_map.get(o_id)
            if not student or not other_user:
                continue

            last_msg = last_msg_map.get((s_id, o_id))
            unread_count = unread_map.get((s_id, o_id), 0)

            class_name = None
            if student.school_class:
                class_name = student.school_class.name

            conversations.append({
                "student_id": s_id,
                "student_name": f"{student.first_name} {student.last_name}",
                "student_photo_path": student.photo_path if hasattr(student, "photo_path") else None,
                "class_name": class_name,
                "other_user_id": o_id,
                "other_user_name": f"{other_user.first_name} {other_user.last_name}",
                "other_user_role": other_user.role,
                "last_message_body": last_msg.body[:100] if last_msg else "",
                "last_message_at": row.last_message_at,
                "last_message_sender_id": last_msg.sender_id if last_msg else None,
                "unread_count": unread_count,
            })

        return conversations, total

    async def get_conversation_messages(
        self,
        db: AsyncSession,
        student_id: uuid.UUID,
        other_user_id: uuid.UUID,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[Message], int]:
        """Get all messages in a conversation, ordered ASC (chat-style).

        Also marks unread messages as read for the current user.
        """
        tenant_id = get_tenant_id()
        user_id = get_current_user_id()

        # All messages between current user and other_user about this student
        query = (
            select(Message)
            .outerjoin(MessageRecipient, MessageRecipient.message_id == Message.id)
            .where(
                Message.tenant_id == tenant_id,
                Message.deleted_at.is_(None),
                Message.student_id == student_id,
                or_(
                    and_(Message.sender_id == user_id, MessageRecipient.user_id == other_user_id),
                    and_(Message.sender_id == other_user_id, MessageRecipient.user_id == user_id),
                ),
            )
        )

        # Count
        count_q = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_q)).scalar() or 0

        # Paginate (most recent messages last)
        query = query.order_by(Message.created_at.asc())
        # For chat, we want the latest page to show recent messages
        # Calculate offset to get the last page if page=1
        if page == 1 and total > page_size:
            offset = total - page_size
            if offset < 0:
                offset = 0
        else:
            offset = (page - 1) * page_size
        query = query.offset(offset).limit(page_size)

        result = await db.execute(query)
        messages = list(result.scalars().unique().all())

        # Mark unread as read
        await self.mark_conversation_read(db, student_id, other_user_id)

        return messages, total

    async def mark_conversation_read(
        self,
        db: AsyncSession,
        student_id: uuid.UUID,
        other_user_id: uuid.UUID,
    ) -> int:
        """Mark all unread messages in a conversation as read for current user."""
        tenant_id = get_tenant_id()
        user_id = get_current_user_id()

        # Get message IDs from other_user in this conversation
        msg_ids_subq = (
            select(Message.id)
            .where(
                Message.tenant_id == tenant_id,
                Message.deleted_at.is_(None),
                Message.student_id == student_id,
                Message.sender_id == other_user_id,
            )
            .subquery()
        )

        stmt = (
            update(MessageRecipient)
            .where(
                MessageRecipient.user_id == user_id,
                MessageRecipient.is_read == False,
                MessageRecipient.message_id.in_(select(msg_ids_subq.c.id)),
            )
            .values(is_read=True, read_at=datetime.now(timezone.utc))
        )
        result = await db.execute(stmt)
        await db.commit()
        return result.rowcount

    async def get_unread_count(
        self,
        db: AsyncSession,
    ) -> int:
        """Get total unread message count for the current user."""
        tenant_id = get_tenant_id()
        user_id = get_current_user_id()

        query = (
            select(func.count(MessageRecipient.id))
            .join(Message, Message.id == MessageRecipient.message_id)
            .where(
                MessageRecipient.user_id == user_id,
                MessageRecipient.is_read == False,
                Message.tenant_id == tenant_id,
                Message.deleted_at.is_(None),
            )
        )
        result = await db.execute(query)
        return result.scalar() or 0

    async def get_compose_context(
        self,
        db: AsyncSession,
    ) -> list[dict]:
        """Get available students and their contactable users for compose form.

        TEACHER: students in their classes with parents.
        PARENT: their children with class teachers.
        ADMIN: all students with parents and teachers.
        """
        tenant_id = get_tenant_id()
        user_id = get_current_user_id()
        role = get_current_user_role()

        result = []

        if role == "PARENT":
            # Get parent's children
            children_q = (
                select(Student)
                .join(ParentStudent, ParentStudent.student_id == Student.id)
                .where(
                    ParentStudent.parent_id == user_id,
                    Student.tenant_id == tenant_id,
                    Student.deleted_at.is_(None),
                    Student.is_active == True,
                )
            )
            children = (await db.execute(children_q)).scalars().all()

            for child in children:
                recipients = []
                if child.class_id:
                    # Get teachers of the child's class
                    teachers_q = (
                        select(User)
                        .join(TeacherClass, TeacherClass.teacher_id == User.id)
                        .where(
                            TeacherClass.class_id == child.class_id,
                            User.is_active == True,
                            User.deleted_at.is_(None),
                        )
                    )
                    teachers = (await db.execute(teachers_q)).scalars().all()
                    for t in teachers:
                        recipients.append({
                            "id": str(t.id),
                            "name": f"{t.first_name} {t.last_name}",
                            "role": t.role,
                        })

                class_name = child.school_class.name if child.school_class else None
                result.append({
                    "student_id": str(child.id),
                    "student_name": f"{child.first_name} {child.last_name}",
                    "class_name": class_name,
                    "recipients": recipients,
                })

        elif role == "TEACHER":
            # Get students in teacher's classes (limited)
            teacher_class_ids_q = select(TeacherClass.class_id).where(
                TeacherClass.teacher_id == user_id,
            )
            teacher_class_ids = [
                row[0] for row in (await db.execute(teacher_class_ids_q)).all()
            ]

            if teacher_class_ids:
                students_q = (
                    select(Student)
                    .where(
                        Student.tenant_id == tenant_id,
                        Student.deleted_at.is_(None),
                        Student.is_active == True,
                        Student.class_id.in_(teacher_class_ids),
                    )
                    .order_by(Student.first_name)
                    .limit(100)
                )
                students = list((await db.execute(students_q)).scalars().all())
                student_ids = [s.id for s in students]

                # Batch-fetch all parents for these students
                parents_map: dict[uuid.UUID, list[dict]] = {sid: [] for sid in student_ids}
                if student_ids:
                    ps_q = (
                        select(ParentStudent.student_id, User)
                        .join(User, ParentStudent.parent_id == User.id)
                        .where(
                            ParentStudent.student_id.in_(student_ids),
                            User.is_active == True,
                            User.deleted_at.is_(None),
                        )
                    )
                    for row in (await db.execute(ps_q)).all():
                        parents_map[row[0]].append({
                            "id": str(row[1].id),
                            "name": f"{row[1].first_name} {row[1].last_name}",
                            "role": row[1].role,
                        })

                # Build class name map
                class_ids = list({s.class_id for s in students if s.class_id})
                class_name_map: dict[uuid.UUID, str] = {}
                if class_ids:
                    cls_q = select(SchoolClass.id, SchoolClass.name).where(
                        SchoolClass.id.in_(class_ids),
                    )
                    for row in (await db.execute(cls_q)).all():
                        class_name_map[row[0]] = row[1]

                for s in students:
                    result.append({
                        "student_id": str(s.id),
                        "student_name": f"{s.first_name} {s.last_name}",
                        "class_name": class_name_map.get(s.class_id),
                        "recipients": parents_map.get(s.id, []),
                    })

        else:
            # SCHOOL_ADMIN / SUPER_ADMIN: students with parents + teachers (limited)
            students_q = (
                select(Student)
                .where(
                    Student.tenant_id == tenant_id,
                    Student.deleted_at.is_(None),
                    Student.is_active == True,
                )
                .order_by(Student.first_name)
                .limit(100)
            )
            students = list((await db.execute(students_q)).scalars().all())
            student_ids = [s.id for s in students]

            # Batch-fetch all parents
            parents_map: dict[uuid.UUID, list[dict]] = {sid: [] for sid in student_ids}
            if student_ids:
                ps_q = (
                    select(ParentStudent.student_id, User)
                    .join(User, ParentStudent.parent_id == User.id)
                    .where(
                        ParentStudent.student_id.in_(student_ids),
                        User.is_active == True,
                        User.deleted_at.is_(None),
                    )
                )
                for row in (await db.execute(ps_q)).all():
                    parents_map[row[0]].append({
                        "id": str(row[1].id),
                        "name": f"{row[1].first_name} {row[1].last_name}",
                        "role": row[1].role,
                    })

            # Batch-fetch all teachers by class
            class_ids = list({s.class_id for s in students if s.class_id})
            teachers_map: dict[uuid.UUID, list[dict]] = {}
            class_name_map: dict[uuid.UUID, str] = {}
            if class_ids:
                tc_q = (
                    select(TeacherClass.class_id, User)
                    .join(User, TeacherClass.teacher_id == User.id)
                    .where(
                        TeacherClass.class_id.in_(class_ids),
                        User.is_active == True,
                        User.deleted_at.is_(None),
                    )
                )
                for row in (await db.execute(tc_q)).all():
                    teachers_map.setdefault(row[0], []).append({
                        "id": str(row[1].id),
                        "name": f"{row[1].first_name} {row[1].last_name}",
                        "role": row[1].role,
                    })
                cls_q = select(SchoolClass.id, SchoolClass.name).where(
                    SchoolClass.id.in_(class_ids),
                )
                for row in (await db.execute(cls_q)).all():
                    class_name_map[row[0]] = row[1]

            for s in students:
                all_recipients = [
                    *parents_map.get(s.id, []),
                    *teachers_map.get(s.class_id, []),
                ]
                recipients = [r for r in all_recipients if r["id"] != str(user_id)]

                result.append({
                    "student_id": str(s.id),
                    "student_name": f"{s.first_name} {s.last_name}",
                    "class_name": class_name_map.get(s.class_id),
                    "recipients": recipients,
                })

        return result

    # ============== Private Helpers ==============

    async def _get_student(
        self,
        db: AsyncSession,
        student_id: uuid.UUID,
    ) -> Student:
        """Get a student by ID within the current tenant."""
        tenant_id = get_tenant_id()
        result = await db.execute(
            select(Student).where(
                Student.id == student_id,
                Student.tenant_id == tenant_id,
                Student.deleted_at.is_(None),
            )
        )
        student = result.scalar_one_or_none()
        if not student:
            from app.exceptions import NotFoundException
            raise NotFoundException("Student not found")
        return student

    async def _validate_can_message(
        self,
        db: AsyncSession,
        sender_id: uuid.UUID,
        sender_role: str,
        student_id: uuid.UUID,
        recipient_id: uuid.UUID,
    ) -> None:
        """Validate that sender can message recipient about this student."""
        from app.exceptions import ForbiddenException

        tenant_id = get_tenant_id()

        if sender_role in ("SUPER_ADMIN", "SCHOOL_ADMIN"):
            # Admins can message anyone in the tenant
            return

        if sender_role == "TEACHER":
            # Student must be in teacher's class
            student = await self._get_student(db, student_id)
            if not student.class_id:
                raise ForbiddenException("Student is not assigned to a class")

            tc_result = await db.execute(
                select(TeacherClass).where(
                    TeacherClass.teacher_id == sender_id,
                    TeacherClass.class_id == student.class_id,
                )
            )
            if not tc_result.scalar_one_or_none():
                raise ForbiddenException("You can only message about students in your classes")

            # Recipient must be a parent of this student
            ps_result = await db.execute(
                select(ParentStudent).where(
                    ParentStudent.parent_id == recipient_id,
                    ParentStudent.student_id == student_id,
                )
            )
            if not ps_result.scalar_one_or_none():
                raise ForbiddenException("Recipient is not a parent of this student")

        elif sender_role == "PARENT":
            # Student must be parent's child
            ps_result = await db.execute(
                select(ParentStudent).where(
                    ParentStudent.parent_id == sender_id,
                    ParentStudent.student_id == student_id,
                )
            )
            if not ps_result.scalar_one_or_none():
                raise ForbiddenException("You can only message about your own children")

            # Recipient must be a teacher of the student's class
            student = await self._get_student(db, student_id)
            if not student.class_id:
                raise ForbiddenException("Student is not assigned to a class")

            tc_result = await db.execute(
                select(TeacherClass).where(
                    TeacherClass.teacher_id == recipient_id,
                    TeacherClass.class_id == student.class_id,
                )
            )
            if not tc_result.scalar_one_or_none():
                raise ForbiddenException("Recipient is not a teacher of this student's class")

        else:
            raise ForbiddenException("You do not have permission to send messages")

    async def _find_conversation_root(
        self,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        student_id: uuid.UUID,
        user_a_id: uuid.UUID,
        user_b_id: uuid.UUID,
    ) -> uuid.UUID | None:
        """Find the first message in a conversation to use as parent_message_id."""
        result = await db.execute(
            select(Message.id)
            .outerjoin(MessageRecipient, MessageRecipient.message_id == Message.id)
            .where(
                Message.tenant_id == tenant_id,
                Message.deleted_at.is_(None),
                Message.student_id == student_id,
                or_(
                    and_(Message.sender_id == user_a_id, MessageRecipient.user_id == user_b_id),
                    and_(Message.sender_id == user_b_id, MessageRecipient.user_id == user_a_id),
                ),
            )
            .order_by(Message.created_at.asc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return row

    async def _send_message_notifications(
        self,
        db: AsyncSession,
        message: Message,
        recipient_id: uuid.UUID,
    ) -> None:
        """Send in-app notification, email, and WebSocket event."""
        from app.services.notification_service import get_notification_service

        notification_service = get_notification_service()

        sender_name = "Unknown"
        if message.sender:
            sender_name = f"{message.sender.first_name} {message.sender.last_name}"

        student_name = ""
        if message.student:
            student_name = f"{message.student.first_name} {message.student.last_name}"

        # In-app notification
        await notification_service.create_notification(
            db=db,
            user_id=recipient_id,
            title=f"New message from {sender_name}",
            body=f"About {student_name}: {message.body[:100]}{'...' if len(message.body) > 100 else ''}",
            notification_type="MESSAGE_RECEIVED",
            reference_type="message",
            reference_id=message.id,
        )

        # Email notification
        try:
            from app.services.email_service import get_email_service

            email_service = get_email_service()

            # Get recipient email
            recipient_result = await db.execute(
                select(User).where(User.id == recipient_id)
            )
            recipient_user = recipient_result.scalar_one_or_none()
            if recipient_user and recipient_user.email:
                # Get tenant name
                from app.models.tenant import Tenant
                tenant = await db.get(Tenant, message.tenant_id)
                tenant_name = tenant.name if tenant else "ClassUp"

                from app.config import get_settings
                settings = get_settings()

                await email_service.send(
                    to=recipient_user.email,
                    subject=f"New message about {student_name}",
                    template_name="message_received.html",
                    context={
                        "sender_name": sender_name,
                        "student_name": student_name,
                        "message_body": message.body[:300],
                        "tenant_name": tenant_name,
                        "app_name": "ClassUp",
                        "base_url": settings.app_base_url,
                    },
                    from_name=tenant_name,
                )
        except Exception as e:
            logger.error(f"Failed to send message email: {e}")

        # WebSocket notification
        try:
            from app.services.realtime_service import get_connection_manager

            manager = get_connection_manager()
            tenant_id = str(message.tenant_id)

            await manager.send_message_received(
                user_id=str(recipient_id),
                tenant_id=tenant_id,
                message_data={
                    "message_id": str(message.id),
                    "sender_name": sender_name,
                    "student_name": student_name,
                    "body_preview": message.body[:100],
                },
            )

            # Update unread count
            unread = await self.get_unread_count(db)
            await manager.send_unread_count(
                user_id=str(recipient_id),
                tenant_id=tenant_id,
                messages=unread,
                notifications=0,
            )
        except Exception as e:
            logger.error(f"Failed to send WebSocket notification: {e}")


# Singleton
_message_service: MessageService | None = None


def get_message_service() -> MessageService:
    """Get the message service singleton."""
    global _message_service
    if _message_service is None:
        _message_service = MessageService()
    return _message_service
