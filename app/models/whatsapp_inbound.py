"""Inbound WhatsApp message log.

Every message Meta delivers to /api/v1/whatsapp/webhook is stored here so the
super admin can watch messages arriving live and confirm the pipeline works
end-to-end. Also handy diagnostic history when a user reports "I sent a
message but nothing happened."

Not tenant-scoped (tenant_id is nullable) because we only know which tenant
a message belongs to after resolving the sender's phone → user → user.tenant.
Unknown senders still get logged (with a null tenant_id + null matched_user_id).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, Boolean
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WhatsAppInboundMessage(Base):
    """One row per inbound WhatsApp message from Meta's webhook."""

    __tablename__ = "whatsapp_inbound_messages"
    __table_args__ = (
        # Meta occasionally retries a webhook — the meta_message_id is stable,
        # so a UNIQUE index gives us free dedup at the DB layer.
        Index(
            "uq_whatsapp_inbound_meta_message_id",
            "meta_message_id",
            unique=True,
            postgresql_where="meta_message_id IS NOT NULL",
        ),
        Index("idx_whatsapp_inbound_created_at", "created_at"),
        Index("idx_whatsapp_inbound_from_phone", "from_phone"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Nullable — we set this once we've matched the sender to a user, since
    # users are tenant-scoped. Unknown senders never get a tenant.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="SET NULL"),
        nullable=True,
    )
    matched_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Always populated by Meta
    from_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    message_type: Mapped[str] = mapped_column(String(30), nullable=False)  # text|button|interactive|image|...
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # The full webhook body (or the message chunk) for debugging. Kept as
    # JSONB so we can query into it later if we ever need to.
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Auto-reply status — for the POC, we always try. Later this holds
    # bot-flow state or "not applicable" for interactive flows.
    auto_replied: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    auto_reply_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
