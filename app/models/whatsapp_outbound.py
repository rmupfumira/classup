"""Outbound WhatsApp message log — one row per send.

Every WhatsApp message the app sends lands here so a super admin can
reconstruct the full conversation with any parent — paired with
``whatsapp_inbound_messages`` on the same phone number.

Written from two call sites:
- ``app/api/v1/whatsapp.py`` — bot replies to a parent's inbound.
  ``inbound_message_id`` is populated so the UI can show "in reply to".
- ``app/services/parent_notifier.py`` — admin-triggered sends
  (attendance alert, invoice sent, event reminder, etc.). No
  ``inbound_message_id``.

Not tenant-scoped (``tenant_id`` nullable) for symmetry with the
inbound table — an admin test send to an unrecognised number still
gets logged with tenant NULL.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WhatsAppOutboundMessage(Base):
    """One row per outbound WhatsApp send."""

    __tablename__ = "whatsapp_outbound_messages"
    __table_args__ = (
        Index(
            "idx_whatsapp_outbound_phone_created",
            "to_phone",
            "created_at",
        ),
        Index(
            "idx_whatsapp_outbound_tenant_created",
            "tenant_id",
            "created_at",
        ),
        Index(
            "idx_whatsapp_outbound_target_user",
            "target_user_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="SET NULL"),
        nullable=True,
    )
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    to_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    message_type: Mapped[str] = mapped_column(String(30), nullable=False)
    template_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    inbound_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("whatsapp_inbound_messages.id", ondelete="SET NULL"),
        nullable=True,
    )

    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    sent_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
