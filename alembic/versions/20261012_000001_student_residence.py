"""Add students.residence column.

Revision ID: 20261012_000001
Revises: 20261009_000003
Create Date: 2026-10-12

Residence designation (DAY / FULL_BOARDER / WEEKLY_BOARDER) for the
boarding-attendance rules. Nullable because only MIXED tenants need
it per-student; pure DAY / BOARDING / WEEKLY_BOARDING tenants derive
the effective residence from the tenant's school_type setting.

No backfill needed — NULL on an existing row means "fall back to the
tenant default", which matches the behaviour every tenant had before
boarding features existed.
"""
from alembic import op
import sqlalchemy as sa

revision = "20261012_000001"
down_revision = "20261009_000003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "students",
        sa.Column("residence", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("students", "residence")
