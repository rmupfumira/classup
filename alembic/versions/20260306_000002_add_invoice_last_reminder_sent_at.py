"""Add last_reminder_sent_at to billing_invoices

Revision ID: 20260306_000002
Revises: 20260306_000001
Create Date: 2026-03-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "20260306_000002"
down_revision: Union[str, None] = "20260306_000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "billing_invoices",
        sa.Column("last_reminder_sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("billing_invoices", "last_reminder_sent_at")
