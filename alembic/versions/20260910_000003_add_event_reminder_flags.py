"""Add reminder_24h_sent_at + reminder_1h_sent_at to school_events.

Revision ID: 20260910_000003
Revises: 20260910_000002
Create Date: 2026-09-10

Two nullable timestamp columns so the reminder worker can idempotently
mark reminders as sent — sweeping the events table with a
"starts_at BETWEEN now+22h AND now+26h AND reminder_24h_sent_at IS NULL"
predicate makes double-sends impossible even if the worker fires more
than once per window.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260910_000003"
down_revision: Union[str, None] = "20260910_000002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "school_events",
        sa.Column("reminder_24h_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "school_events",
        sa.Column("reminder_1h_sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("school_events", "reminder_1h_sent_at")
    op.drop_column("school_events", "reminder_24h_sent_at")
