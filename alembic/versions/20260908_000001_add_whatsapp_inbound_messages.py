"""Add whatsapp_inbound_messages table for the WhatsApp bot POC.

Revision ID: 20260908_000001
Revises: 20260513_000001
Create Date: 2026-09-08

Every inbound WhatsApp message lands here so the super admin can watch
the pipeline work live. Unique index on meta_message_id gives free
dedup against Meta's retries.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "20260908_000001"
down_revision: Union[str, None] = "20260513_000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "whatsapp_inbound_messages",
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
            "matched_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("from_phone", sa.String(32), nullable=False),
        sa.Column("message_type", sa.String(30), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("meta_message_id", sa.String(128), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=True),
        sa.Column(
            "auto_replied",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("auto_reply_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "uq_whatsapp_inbound_meta_message_id",
        "whatsapp_inbound_messages",
        ["meta_message_id"],
        unique=True,
        postgresql_where=sa.text("meta_message_id IS NOT NULL"),
    )
    op.create_index(
        "idx_whatsapp_inbound_created_at",
        "whatsapp_inbound_messages",
        ["created_at"],
    )
    op.create_index(
        "idx_whatsapp_inbound_from_phone",
        "whatsapp_inbound_messages",
        ["from_phone"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_whatsapp_inbound_from_phone",
        table_name="whatsapp_inbound_messages",
    )
    op.drop_index(
        "idx_whatsapp_inbound_created_at",
        table_name="whatsapp_inbound_messages",
    )
    op.drop_index(
        "uq_whatsapp_inbound_meta_message_id",
        table_name="whatsapp_inbound_messages",
    )
    op.drop_table("whatsapp_inbound_messages")
