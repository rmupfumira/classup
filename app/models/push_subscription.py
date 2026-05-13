"""Web Push subscription model — one row per user × device.

When a user enables push notifications in the browser, `pushManager.subscribe()`
returns:
    {
        endpoint: "https://fcm.googleapis.com/fcm/send/...",
        keys: { p256dh: "...", auth: "..." }
    }

We store these so the server can later POST encrypted payloads via pywebpush.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import TenantScopedModel


class PushSubscription(TenantScopedModel):
    """A user's subscription to push notifications on a specific device/browser.

    `endpoint` is unique — re-subscribing from the same device (e.g. after a
    permission reset) returns the same endpoint, so we treat that as an
    upsert rather than a duplicate row.
    """

    __tablename__ = "push_subscriptions"
    __table_args__ = (
        # User × tenant lookups are the hot path (sending a push to a user)
        Index("idx_push_subs_user", "tenant_id", "user_id"),
        # Endpoint uniqueness is enforced at the column level below
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # The push service URL — Apple's, FCM, Mozilla's, etc. Unique so the
    # same browser re-subscribing doesn't create duplicates.
    endpoint: Mapped[str] = mapped_column(String(2000), nullable=False, unique=True)

    # The two encryption keys the push service expects when we send a payload.
    # Both are base64url strings. p256dh ≈ 88 chars, auth ≈ 24 chars.
    p256dh: Mapped[str] = mapped_column(String(256), nullable=False)
    auth: Mapped[str] = mapped_column(String(64), nullable=False)

    # Diagnostics — helps when subscribers complain "I'm not getting pushes"
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )
    last_failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Relationship for ORM convenience
    user = relationship("User", lazy="selectin")

    def __repr__(self) -> str:
        # Truncate endpoint — fcm URLs are very long and noisy in logs
        ep = self.endpoint or ""
        if len(ep) > 60:
            ep = ep[:57] + "..."
        return f"<PushSubscription user={self.user_id} endpoint={ep}>"
