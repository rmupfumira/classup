"""Add whatsapp_outbound_messages so we can reconstruct conversations.

Revision ID: 20260912_000001
Revises: 20260910_000003
Create Date: 2026-09-12

Every WhatsApp message the app sends (bot reply, parent notification,
admin-triggered) gets a row here. Combined with the inbound table
that already exists (``whatsapp_inbound_messages``), super admin can
now view a full conversation per parent / phone number — instead of
just the one-sided view of what parents wrote in.

Indexes chosen for the two dominant queries:
- conversations list: latest message per phone → ``(to_phone,
  created_at DESC)`` covering index
- one thread: chronological messages for a phone → same index reused
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260912_000001"
down_revision: Union[str, None] = "20260910_000003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "whatsapp_outbound_messages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "target_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # to_phone is authoritative — target_user_id may be NULL for
        # admin test sends or unlinked numbers.
        sa.Column("to_phone", sa.String(32), nullable=False),
        # Category of send — text / template / interactive_buttons /
        # interactive_list / document / image.
        sa.Column("message_type", sa.String(30), nullable=False),
        # Non-null for template sends.
        sa.Column("template_name", sa.String(80), nullable=True),
        # Rendered / visible body copy for the conversation view. For
        # templates it's the (best-effort) filled body; for text sends
        # it's the raw body. Truncated to 8000 chars like the inbound
        # column, so a giant document caption doesn't blow the row.
        sa.Column("body_text", sa.Text(), nullable=True),
        # Meta returns this on 200 OK — used to correlate delivery
        # receipts later, and NULL when a send fails.
        sa.Column("meta_message_id", sa.String(128), nullable=True),
        # If this send was a bot reply to a specific inbound, link it.
        # Lets the UI render "reply to" context.
        sa.Column(
            "inbound_message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "whatsapp_inbound_messages.id",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
        # Populated on send failure — Meta's error message, or the
        # exception summary.
        sa.Column("error", sa.Text(), nullable=True),
        # For admin-triggered sends: which admin/staff triggered this.
        # NULL for automated flows (attendance alerts, event reminders,
        # bot replies).
        sa.Column(
            "sent_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "idx_whatsapp_outbound_phone_created",
        "whatsapp_outbound_messages",
        ["to_phone", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_whatsapp_outbound_tenant_created",
        "whatsapp_outbound_messages",
        ["tenant_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_whatsapp_outbound_target_user",
        "whatsapp_outbound_messages",
        ["target_user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_whatsapp_outbound_target_user",
        table_name="whatsapp_outbound_messages",
    )
    op.drop_index(
        "idx_whatsapp_outbound_tenant_created",
        table_name="whatsapp_outbound_messages",
    )
    op.drop_index(
        "idx_whatsapp_outbound_phone_created",
        table_name="whatsapp_outbound_messages",
    )
    op.drop_table("whatsapp_outbound_messages")
