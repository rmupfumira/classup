"""Add billing tables (fee_items, invoices, invoice_items, payments)

Revision ID: 20260306_000001
Revises: 20260304_000003
Create Date: 2026-03-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260306_000001"
down_revision: Union[str, None] = "20260304_000003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- billing_fee_items ---
    op.create_table(
        "billing_fee_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("frequency", sa.String(length=20), nullable=False, server_default="MONTHLY"),
        sa.Column("applies_to", sa.String(length=20), nullable=False, server_default="ALL"),
        sa.Column("class_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["class_id"], ["school_classes.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_billing_fee_items_tenant_id", "billing_fee_items", ["tenant_id"])
    op.create_index(
        "idx_billing_fee_items_tenant_active",
        "billing_fee_items",
        ["tenant_id"],
        postgresql_where=sa.text("deleted_at IS NULL AND is_active = true"),
    )

    # --- billing_invoices ---
    op.create_table(
        "billing_invoices",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("student_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_number", sa.String(length=30), nullable=False),
        sa.Column("billing_period_start", sa.Date(), nullable=True),
        sa.Column("billing_period_end", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("subtotal", sa.Numeric(precision=12, scale=2), nullable=False, server_default="0.00"),
        sa.Column("total_amount", sa.Numeric(precision=12, scale=2), nullable=False, server_default="0.00"),
        sa.Column("amount_paid", sa.Numeric(precision=12, scale=2), nullable=False, server_default="0.00"),
        sa.Column("balance", sa.Numeric(precision=12, scale=2), nullable=False, server_default="0.00"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="DRAFT"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_billing_invoices_tenant_id", "billing_invoices", ["tenant_id"])
    op.create_index(
        "idx_billing_invoices_tenant_status",
        "billing_invoices",
        ["tenant_id", "status"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_billing_invoices_student",
        "billing_invoices",
        ["tenant_id", "student_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_billing_invoices_number",
        "billing_invoices",
        ["tenant_id", "invoice_number"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # --- billing_invoice_items ---
    op.create_table(
        "billing_invoice_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fee_item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("unit_amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("total_amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["invoice_id"], ["billing_invoices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["fee_item_id"], ["billing_fee_items.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_billing_invoice_items_invoice_id", "billing_invoice_items", ["invoice_id"])

    # --- billing_payments ---
    op.create_table(
        "billing_payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("student_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("payment_method", sa.String(length=30), nullable=False, server_default="CASH"),
        sa.Column("reference_number", sa.String(length=100), nullable=True),
        sa.Column("payment_date", sa.Date(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("recorded_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invoice_id"], ["billing_invoices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recorded_by"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_billing_payments_tenant_id", "billing_payments", ["tenant_id"])
    op.create_index("idx_billing_payments_invoice", "billing_payments", ["invoice_id"])
    op.create_index(
        "idx_billing_payments_student",
        "billing_payments",
        ["tenant_id", "student_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("billing_payments")
    op.drop_index("ix_billing_invoice_items_invoice_id", table_name="billing_invoice_items")
    op.drop_table("billing_invoice_items")
    op.drop_index("idx_billing_invoices_number", table_name="billing_invoices")
    op.drop_index("idx_billing_invoices_student", table_name="billing_invoices")
    op.drop_index("idx_billing_invoices_tenant_status", table_name="billing_invoices")
    op.drop_index("ix_billing_invoices_tenant_id", table_name="billing_invoices")
    op.drop_table("billing_invoices")
    op.drop_index("idx_billing_fee_items_tenant_active", table_name="billing_fee_items")
    op.drop_index("ix_billing_fee_items_tenant_id", table_name="billing_fee_items")
    op.drop_table("billing_fee_items")
