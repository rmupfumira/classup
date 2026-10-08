"""Add ai_tool_calls telemetry table.

Revision ID: 20261008_000001
Revises: 20261006_000002
Create Date: 2026-10-08

One row per tool invocation from the WhatsApp AI bot. Added as part
of the Principal AI review hardening to make per-tenant / per-user
usage observable, enable cost reconstruction, and surface tool
outcome analytics (e.g. authorization-fail rate).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261008_000001"
down_revision = "20261006_000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_tool_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("whatsapp_message_id", sa.String(length=100), nullable=True),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("args_hash", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "idx_ai_tool_calls_tenant_created",
        "ai_tool_calls",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "idx_ai_tool_calls_whatsapp_message",
        "ai_tool_calls",
        ["whatsapp_message_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_ai_tool_calls_whatsapp_message", table_name="ai_tool_calls")
    op.drop_index("idx_ai_tool_calls_tenant_created", table_name="ai_tool_calls")
    op.drop_table("ai_tool_calls")
