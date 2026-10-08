"""AI tool-call telemetry — one row per tool invocation from the bot.

Added 2026-10-08 as part of the Principal AI review hardening. Enables:
- Per-parent / per-tenant usage dashboards.
- Cost reconstruction (token totals × model unit price).
- Outcome analytics (what % of tool calls returned ``not_allowed``,
  which is a canary for parent confusion or injection attempts).
- Debugging: find every call a model made for one inbound.

Not tenant-scoped (``tenant_id`` nullable) because a mis-matched sender
can still trigger a tool-use branch during development; the row survives
so operators can see what happened.

Writes are best-effort and happen OUTSIDE the model loop — the
telemetry service never raises back into the dispatcher.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AIToolCall(Base):
    """One row per tool invocation (success or failure)."""

    __tablename__ = "ai_tool_calls"
    __table_args__ = (
        # Dashboards query "last N days for tenant X" → covering index.
        Index(
            "idx_ai_tool_calls_tenant_created",
            "tenant_id",
            "created_at",
        ),
        # "Everything that happened for one inbound message" lookup.
        Index(
            "idx_ai_tool_calls_whatsapp_message",
            "whatsapp_message_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="SET NULL"),
        nullable=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Links back to the inbound that triggered the loop so operators can
    # pivot from "what the parent said" to "what tools fired".
    whatsapp_message_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True,
    )
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    # Stable sha256 of (name, sorted-args). Not the args themselves —
    # args can contain child_ids and we don't want PII in telemetry.
    args_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Mirrors ToolErrorCode plus "ok" / "deduped".
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
