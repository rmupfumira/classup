"""Add invoice_only flag to chart_accounts.

An INCOME account marked invoice_only=True can only receive income via
the billing flow (record a payment against an invoice, which then
auto-creates the matching AccountingTransaction). Direct manual income
entries against it are rejected at the service layer and hidden from
the standard Record-income dropdown.

Backfill: the existing seeded Tuition Fees row (code 4000, which is
is_system=True) is flipped to invoice_only=True. Any custom account
the tenant created themselves stays invoice_only=False and can be
toggled from the Chart of Accounts editor.

Revision ID: 20261119_000001
Revises: 20261118_000002
Create Date: 2026-11-19
"""

from alembic import op
import sqlalchemy as sa

revision = "20261119_000001"
down_revision = "20261118_000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chart_accounts",
        sa.Column(
            "invoice_only",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # Backfill: the seeded Tuition Fees account per tenant (is_system=True
    # + type=INCOME) is the billing auto-link target, so it's invoice-only
    # by definition.
    op.execute(
        "UPDATE chart_accounts SET invoice_only = true "
        "WHERE is_system = true AND type = 'INCOME'"
    )


def downgrade() -> None:
    op.drop_column("chart_accounts", "invoice_only")
