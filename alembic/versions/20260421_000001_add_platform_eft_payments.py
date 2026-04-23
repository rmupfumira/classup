"""Add platform_eft_payments table for manual EFT payment flow.

Revision ID: 20260421_000001
Revises: 20260409_000001
Create Date: 2026-04-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "20260421_000001"
down_revision: Union[str, None] = "20260409_000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "platform_eft_payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subscription_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("platform_invoice_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="ZAR"),
        sa.Column("reference", sa.String(length=100), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("pop_file_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("extend_period_days", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["subscription_id"], ["tenant_subscriptions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["platform_invoice_id"], ["platform_invoices.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["pop_file_id"], ["file_entities.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "idx_platform_eft_tenant", "platform_eft_payments", ["tenant_id"]
    )
    op.create_index(
        "idx_platform_eft_status", "platform_eft_payments", ["status"]
    )
    op.create_index(
        "idx_platform_eft_submitted", "platform_eft_payments", ["submitted_at"]
    )
    op.create_index(
        "ix_platform_eft_payments_tenant_id",
        "platform_eft_payments",
        ["tenant_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_platform_eft_payments_tenant_id", table_name="platform_eft_payments")
    op.drop_index("idx_platform_eft_submitted", table_name="platform_eft_payments")
    op.drop_index("idx_platform_eft_status", table_name="platform_eft_payments")
    op.drop_index("idx_platform_eft_tenant", table_name="platform_eft_payments")
    op.drop_table("platform_eft_payments")
