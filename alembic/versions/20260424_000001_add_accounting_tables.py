"""Add accounting tables.

Revision ID: 20260424_000001
Revises: 20260423_000001
Create Date: 2026-04-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "20260424_000001"
down_revision: Union[str, None] = "20260423_000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- chart_accounts ----
    op.create_table(
        "chart_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_chart_accounts_tenant_code"),
    )
    op.create_index("ix_chart_accounts_tenant_id", "chart_accounts", ["tenant_id"])
    op.create_index("idx_chart_accounts_tenant_type", "chart_accounts", ["tenant_id", "type"])

    # ---- bank_accounts ----
    op.create_table(
        "bank_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("bank_name", sa.String(length=120), nullable=True),
        sa.Column("account_number", sa.String(length=50), nullable=True),
        sa.Column("branch_code", sa.String(length=20), nullable=True),
        sa.Column("account_type", sa.String(length=20), nullable=False, server_default="OPERATING"),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="ZAR"),
        sa.Column("opening_balance", sa.Numeric(precision=14, scale=2), nullable=False, server_default="0"),
        sa.Column("opening_balance_date", sa.Date(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_bank_accounts_tenant_id", "bank_accounts", ["tenant_id"])
    op.create_index("idx_bank_accounts_tenant_active", "bank_accounts", ["tenant_id", "is_active"])

    # ---- vendors ----
    op.create_table(
        "vendors",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("contact_person", sa.String(length=150), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("vat_number", sa.String(length=40), nullable=True),
        sa.Column("banking_details", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_vendors_tenant_id", "vendors", ["tenant_id"])
    op.create_index("idx_vendors_tenant_active", "vendors", ["tenant_id", "is_active"])

    # ---- accounting_transactions ----
    op.create_table(
        "accounting_transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="ZAR"),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("bank_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transfer_to_bank_account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("student_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("billing_payment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("reference", sa.String(length=100), nullable=True),
        sa.Column("vat_amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("vat_rate", sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column("receipt_file_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["chart_accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["bank_account_id"], ["bank_accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["transfer_to_bank_account_id"], ["bank_accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["vendor_id"], ["vendors.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["billing_payment_id"], ["billing_payments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["receipt_file_id"], ["file_entities.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("billing_payment_id", name="uq_accounting_tx_billing_payment"),
    )
    op.create_index("ix_accounting_transactions_tenant_id", "accounting_transactions", ["tenant_id"])
    op.create_index("idx_accounting_tx_tenant_date", "accounting_transactions", ["tenant_id", "date"])
    op.create_index("idx_accounting_tx_account", "accounting_transactions", ["account_id"])
    op.create_index("idx_accounting_tx_bank", "accounting_transactions", ["bank_account_id"])
    op.create_index("idx_accounting_tx_billing_payment", "accounting_transactions", ["billing_payment_id"])


def downgrade() -> None:
    op.drop_index("idx_accounting_tx_billing_payment", table_name="accounting_transactions")
    op.drop_index("idx_accounting_tx_bank", table_name="accounting_transactions")
    op.drop_index("idx_accounting_tx_account", table_name="accounting_transactions")
    op.drop_index("idx_accounting_tx_tenant_date", table_name="accounting_transactions")
    op.drop_index("ix_accounting_transactions_tenant_id", table_name="accounting_transactions")
    op.drop_table("accounting_transactions")

    op.drop_index("idx_vendors_tenant_active", table_name="vendors")
    op.drop_index("ix_vendors_tenant_id", table_name="vendors")
    op.drop_table("vendors")

    op.drop_index("idx_bank_accounts_tenant_active", table_name="bank_accounts")
    op.drop_index("ix_bank_accounts_tenant_id", table_name="bank_accounts")
    op.drop_table("bank_accounts")

    op.drop_index("idx_chart_accounts_tenant_type", table_name="chart_accounts")
    op.drop_index("ix_chart_accounts_tenant_id", table_name="chart_accounts")
    op.drop_table("chart_accounts")
