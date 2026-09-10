"""Add billing_frequency to tenant_subscriptions.

Revision ID: 20260910_000001
Revises: 20260908_000001
Create Date: 2026-09-10

Tenants can now pick monthly or annual billing when they opt out of
the trial. The frequency lives on the subscription row so the renewal
worker (future) knows the cadence, and invoice-creation reads it to
decide the amount + period length.

Existing rows default to MONTHLY — matches historical behaviour where
invoices always covered 30 days.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260910_000001"
down_revision: Union[str, None] = "20260908_000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenant_subscriptions",
        sa.Column(
            "billing_frequency",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'MONTHLY'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tenant_subscriptions", "billing_frequency")
