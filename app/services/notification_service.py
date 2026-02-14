"""Notification service for managing in-app notifications."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification
from app.utils.tenant_context import get_tenant_id


class NotificationService:
    """Service for managing user notifications."""

    async def get_notifications(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        unread_only: bool = False,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Notification], int]:
        """Get notifications for a user."""
        tenant_id = get_tenant_id()

        # Build query
        query = select(Notification).where(
            Notification.user_id == user_id,
            Notification.tenant_id == tenant_id,
        )

        if unread_only:
            query = query.where(Notification.is_read == False)

        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_query)).scalar() or 0

        # Apply pagination and ordering
        query = query.order_by(Notification.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(query)
        notifications = list(result.scalars().all())

        return notifications, total

    async def get_unread_count(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
    ) -> int:
        """Get the count of unread notifications for a user."""
        tenant_id = get_tenant_id()

        query = select(func.count(Notification.id)).where(
            Notification.user_id == user_id,
            Notification.tenant_id == tenant_id,
            Notification.is_read == False,
        )

        result = await db.execute(query)
        return result.scalar() or 0

    async def create_notification(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        title: str,
        body: str,
        notification_type: str,
        reference_type: str | None = None,
        reference_id: uuid.UUID | None = None,
    ) -> Notification:
        """Create a new notification for a user."""
        tenant_id = get_tenant_id()

        notification = Notification(
            tenant_id=tenant_id,
            user_id=user_id,
            title=title,
            body=body,
            notification_type=notification_type,
            reference_type=reference_type,
            reference_id=reference_id,
        )

        db.add(notification)
        await db.commit()
        await db.refresh(notification)

        return notification

    async def create_bulk_notifications(
        self,
        db: AsyncSession,
        user_ids: list[uuid.UUID],
        title: str,
        body: str,
        notification_type: str,
        reference_type: str | None = None,
        reference_id: uuid.UUID | None = None,
    ) -> list[Notification]:
        """Create notifications for multiple users."""
        tenant_id = get_tenant_id()

        notifications = []
        for user_id in user_ids:
            notification = Notification(
                tenant_id=tenant_id,
                user_id=user_id,
                title=title,
                body=body,
                notification_type=notification_type,
                reference_type=reference_type,
                reference_id=reference_id,
            )
            db.add(notification)
            notifications.append(notification)

        await db.commit()

        for notification in notifications:
            await db.refresh(notification)

        return notifications

    async def mark_as_read(
        self,
        db: AsyncSession,
        notification_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> bool:
        """Mark a notification as read."""
        tenant_id = get_tenant_id()

        stmt = (
            update(Notification)
            .where(
                Notification.id == notification_id,
                Notification.user_id == user_id,
                Notification.tenant_id == tenant_id,
            )
            .values(is_read=True, read_at=datetime.now(timezone.utc))
        )

        result = await db.execute(stmt)
        await db.commit()

        return result.rowcount > 0

    async def mark_all_as_read(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
    ) -> int:
        """Mark all notifications as read for a user."""
        tenant_id = get_tenant_id()

        stmt = (
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.tenant_id == tenant_id,
                Notification.is_read == False,
            )
            .values(is_read=True, read_at=datetime.now(timezone.utc))
        )

        result = await db.execute(stmt)
        await db.commit()

        return result.rowcount

    async def delete_notification(
        self,
        db: AsyncSession,
        notification_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> bool:
        """Delete a notification."""
        tenant_id = get_tenant_id()

        query = select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == user_id,
            Notification.tenant_id == tenant_id,
        )

        result = await db.execute(query)
        notification = result.scalar_one_or_none()

        if not notification:
            return False

        await db.delete(notification)
        await db.commit()

        return True

    # ============== Notification Type Helpers ==============

    async def notify_attendance_marked(
        self,
        db: AsyncSession,
        parent_ids: list[uuid.UUID],
        student_name: str,
        status: str,
    ) -> list[Notification]:
        """Send attendance notification to parents."""
        return await self.create_bulk_notifications(
            db=db,
            user_ids=parent_ids,
            title=f"Attendance: {student_name}",
            body=f"{student_name} was marked {status} today.",
            notification_type="ATTENDANCE_MARKED",
        )

    async def notify_report_finalized(
        self,
        db: AsyncSession,
        parent_ids: list[uuid.UUID],
        student_name: str,
        report_type: str,
        report_id: uuid.UUID,
    ) -> list[Notification]:
        """Send report notification to parents."""
        return await self.create_bulk_notifications(
            db=db,
            user_ids=parent_ids,
            title=f"New Report: {student_name}",
            body=f"A new {report_type} is ready for {student_name}.",
            notification_type="REPORT_FINALIZED",
            reference_type="report",
            reference_id=report_id,
        )



# Singleton instance
_notification_service: NotificationService | None = None


def get_notification_service() -> NotificationService:
    """Get the notification service singleton."""
    global _notification_service
    if _notification_service is None:
        _notification_service = NotificationService()
    return _notification_service
