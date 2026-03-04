"""Message service for teacher-parent conversations scoped to students."""

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import and_, func, or_, select, update
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
        """Send a new message (always starts a new thread)."""
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

        # Each new message starts a fresh thread (parent_message_id=None)
        message = Message(
            tenant_id=tenant_id,
            sender_id=sender_id,
            message_type=MessageType.STUDENT_MESSAGE.value,
            subject=subject,
            body=body,
            student_id=student_id,
            class_id=class_id,
            parent_message_id=None,
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

    async def reply_to_thread(
        self,
        db: AsyncSession,
        thread_id: uuid.UUID,
        body: str,
    ) -> Message:
        """Reply to an existing thread (identified by root message ID)."""
        tenant_id = get_tenant_id()
        sender_id = get_current_user_id()
        role = get_current_user_role()

        # Load the root message to get student_id and determine the other user
        root_msg = await db.execute(
            select(Message)
            .options(selectinload(Message.recipients))
            .where(
                Message.id == thread_id,
                Message.tenant_id == tenant_id,
                Message.deleted_at.is_(None),
                Message.parent_message_id.is_(None),
            )
        )
        root = root_msg.scalar_one_or_none()
        if not root:
            raise ValueError("Thread not found")

        # Determine the other user (whoever isn't the current sender)
        if root.sender_id == sender_id:
            other_user_id = root.recipients[0].user_id if root.recipients else None
        else:
            other_user_id = root.sender_id

        if not other_user_id:
            raise ValueError("Cannot determine recipient")

        await self._validate_can_message(db, sender_id, role, root.student_id, other_user_id)

        student = await self._get_student(db, root.student_id)

        message = Message(
            tenant_id=tenant_id,
            sender_id=sender_id,
            message_type=MessageType.REPLY.value,
            body=body,
            student_id=root.student_id,
            class_id=student.class_id,
            parent_message_id=thread_id,
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
        """Get inbox conversations for the current user, grouped by thread."""
        tenant_id = get_tenant_id()
        user_id = get_current_user_id()

        # thread_id = root message id. For root messages it's their own id,
        # for replies it's parent_message_id.
        thread_id_expr = func.coalesce(Message.parent_message_id, Message.id)

        # Messages where user is sender or recipient
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

        # Get distinct threads with last message time
        threads_q = (
            select(
                thread_id_expr.label("thread_id"),
                func.max(Message.created_at).label("last_message_at"),
            )
            .where(base_filter)
            .group_by(thread_id_expr)
        )

        # Count total threads
        count_q = select(func.count()).select_from(threads_q.subquery())
        total = (await db.execute(count_q)).scalar() or 0

        # Paginated
        threads_q = threads_q.order_by(func.max(Message.created_at).desc())
        threads_q = threads_q.offset((page - 1) * page_size).limit(page_size)
        thread_rows = (await db.execute(threads_q)).all()

        if not thread_rows:
            return [], total

        thread_ids = [row.thread_id for row in thread_rows]

        # Fetch root messages (gives us student_id, subject, sender, recipient)
        root_msgs_result = await db.execute(
            select(Message)
            .options(selectinload(Message.recipients))
            .where(Message.id.in_(thread_ids))
        )
        root_msgs = {m.id: m for m in root_msgs_result.scalars().all()}

        # Determine other_user per thread
        other_user_map: dict[uuid.UUID, uuid.UUID | None] = {}
        student_ids_set: set[uuid.UUID] = set()
        for tid in thread_ids:
            root = root_msgs.get(tid)
            if not root:
                continue
            if root.student_id:
                student_ids_set.add(root.student_id)
            if root.sender_id == user_id:
                other_user_map[tid] = root.recipients[0].user_id if root.recipients else None
            else:
                other_user_map[tid] = root.sender_id

        student_ids = list(student_ids_set)
        other_user_ids = list({uid for uid in other_user_map.values() if uid})

        # Batch-fetch students and users
        students_result = await db.execute(
            select(Student).where(Student.id.in_(student_ids))
        )
        students_map = {s.id: s for s in students_result.scalars().all()}

        users_result = await db.execute(
            select(User).where(User.id.in_(other_user_ids))
        )
        users_map = {u.id: u for u in users_result.scalars().all()}

        # Batch-fetch unread counts per thread
        unread_thread_expr = func.coalesce(Message.parent_message_id, Message.id)
        unread_q = (
            select(
                unread_thread_expr.label("thread_id"),
                func.count(MessageRecipient.id).label("cnt"),
            )
            .join(Message, Message.id == MessageRecipient.message_id)
            .where(
                MessageRecipient.user_id == user_id,
                MessageRecipient.is_read == False,
                Message.tenant_id == tenant_id,
                Message.deleted_at.is_(None),
                unread_thread_expr.in_(thread_ids),
            )
            .group_by(unread_thread_expr)
        )
        unread_rows = (await db.execute(unread_q)).all()
        unread_map = {r.thread_id: r.cnt for r in unread_rows}

        # Fetch last message per thread
        last_msg_q = (
            select(Message)
            .where(
                Message.tenant_id == tenant_id,
                Message.deleted_at.is_(None),
                or_(
                    Message.id.in_(thread_ids),
                    Message.parent_message_id.in_(thread_ids),
                ),
            )
            .order_by(Message.created_at.desc())
        )
        last_msg_result = await db.execute(last_msg_q)
        all_thread_msgs = last_msg_result.scalars().unique().all()

        last_msg_map: dict[uuid.UUID, Message] = {}
        for msg in all_thread_msgs:
            tid = msg.parent_message_id if msg.parent_message_id else msg.id
            if tid not in last_msg_map:
                last_msg_map[tid] = msg

        # Build response
        conversations = []
        for row in thread_rows:
            tid = row.thread_id
            root = root_msgs.get(tid)
            if not root:
                continue

            s_id = root.student_id
            o_id = other_user_map.get(tid)

            student = students_map.get(s_id)
            other_user = users_map.get(o_id) if o_id else None
            if not student or not other_user:
                continue

            last_msg = last_msg_map.get(tid)

            class_name = None
            if student.school_class:
                class_name = student.school_class.name

            conversations.append({
                "thread_id": tid,
                "student_id": s_id,
                "student_name": f"{student.first_name} {student.last_name}",
                "student_photo_path": student.photo_path if hasattr(student, "photo_path") else None,
                "class_name": class_name,
                "other_user_id": o_id,
                "other_user_name": f"{other_user.first_name} {other_user.last_name}",
                "other_user_role": other_user.role,
                "subject": root.subject,
                "last_message_body": last_msg.body[:100] if last_msg else "",
                "last_message_at": row.last_message_at,
                "last_message_sender_id": last_msg.sender_id if last_msg else None,
                "unread_count": unread_map.get(tid, 0),
            })

        return conversations, total

    async def get_conversation_messages(
        self,
        db: AsyncSession,
        thread_id: uuid.UUID,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[Message], int]:
        """Get all messages in a thread, ordered ASC (chat-style).

        Also marks unread messages as read for the current user.
        """
        tenant_id = get_tenant_id()

        # All messages in this thread (root + replies)
        query = (
            select(Message)
            .where(
                Message.tenant_id == tenant_id,
                Message.deleted_at.is_(None),
                or_(
                    Message.id == thread_id,
                    Message.parent_message_id == thread_id,
                ),
            )
        )

        # Count
        count_q = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_q)).scalar() or 0

        # Paginate (most recent messages last)
        query = query.order_by(Message.created_at.asc())
        # For chat, we want the latest page to show recent messages
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
        await self.mark_conversation_read(db, thread_id)

        return messages, total

    async def mark_conversation_read(
        self,
        db: AsyncSession,
        thread_id: uuid.UUID,
    ) -> int:
        """Mark all unread messages in a thread as read for current user."""
        tenant_id = get_tenant_id()
        user_id = get_current_user_id()

        # Get message IDs in this thread sent by others
        msg_ids_subq = (
            select(Message.id)
            .where(
                Message.tenant_id == tenant_id,
                Message.deleted_at.is_(None),
                or_(
                    Message.id == thread_id,
                    Message.parent_message_id == thread_id,
                ),
                Message.sender_id != user_id,
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
