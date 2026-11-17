"""Add applies_to_term to billing_fee_items.

Nullable integer. Admins pin a TERMLY fee item to a specific term number
(1..4) — invoice generation skips it when the invoice's billing period
lands outside that term, and the line description auto-labels "Term N
YYYY" using the jurisdiction calendar.

Revision ID: 20261117_000001
Revises: 20261027_000001
Create Date: 2026-11-17
"""

from alembic import op
import sqlalchemy as sa

revision = "20261117_000001"
down_revision = "20261027_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "billing_fee_items",
        sa.Column("applies_to_term", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("billing_fee_items", "applies_to_term")
