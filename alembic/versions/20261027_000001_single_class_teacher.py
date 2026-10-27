"""Normalise teacher_classes: one class teacher per class.

Revision ID: 20261027_000001
Revises: 20261015_000001
Create Date: 2026-10-27

Policy change (user directive): a class has exactly ONE class teacher.
Everyone else contributing is a subject teacher via
``class_subject_teachers``. The ``is_primary`` column on teacher_classes
is kept for backwards compat but is always True from here on.

This migration cleans up pre-existing data where a class could have
multiple general teachers (primary + co-teachers). For every class:
  - If a primary teacher exists, keep that row and delete the rest.
  - Otherwise keep the earliest-assigned row and delete the rest.
  - The surviving row is forced is_primary=True.

Idempotent — a tenant already following the one-row rule gets no deletes.
"""
from alembic import op
import sqlalchemy as sa


revision = "20261027_000001"
down_revision = "20261015_000001"
branch_labels = None
depends_on = None


def upgrade():
    # Keep (per class) the primary row if any, else the earliest assigned.
    # Delete everything else. Then force is_primary=True on survivors.
    op.execute("""
        DELETE FROM teacher_classes tc
        WHERE tc.id NOT IN (
            SELECT keeper.id FROM (
                SELECT DISTINCT ON (class_id) id
                FROM teacher_classes
                ORDER BY class_id,
                         is_primary DESC NULLS LAST,
                         assigned_at ASC
            ) keeper
        )
    """)
    op.execute("UPDATE teacher_classes SET is_primary = TRUE WHERE is_primary = FALSE")


def downgrade():
    # Non-reversible — we deleted co-teacher rows. No-op downgrade.
    pass
