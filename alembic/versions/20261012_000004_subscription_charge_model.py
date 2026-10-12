"""Add charge_model + per_student_amount to subscription_plans.

Revision ID: 20261012_000004
Revises: 20261012_000003
Create Date: 2026-10-12

Lets ClassUp charge tenants on a FLAT / PER_STUDENT / HYBRID basis
instead of only the fixed per-cycle amount every plan has used until
now. See the ChargeModel enum in app/models/subscription.py for the
semantics.

Backfilled to FLAT for every existing row — matches the pre-2026-10-12
behaviour where every plan was effectively FLAT (price_monthly /
price_annually was the only charge).
"""
from alembic import op
import sqlalchemy as sa

revision = "20261012_000004"
down_revision = "20261012_000003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "subscription_plans",
        sa.Column(
            "charge_model",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'FLAT'"),
        ),
    )
    op.add_column(
        "subscription_plans",
        sa.Column(
            "per_student_amount",
            sa.Numeric(12, 2),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("subscription_plans", "per_student_amount")
    op.drop_column("subscription_plans", "charge_model")
