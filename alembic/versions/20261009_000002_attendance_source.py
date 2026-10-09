"""Add attendance_records.source column.

Revision ID: 20261009_000002
Revises: 20261009_000001
Create Date: 2026-10-09

Distinguishes teacher-marked attendance (SCHOOL) from parent-reported
absences (PARENT_REPORTED). The parent's "Report an absence" quick
action on the dashboard writes rows with ``source=PARENT_REPORTED``;
the teacher's attendance view surfaces those with a visible badge so
staff can override if the record is wrong (e.g. the child did show up
after all).

Backfilled to SCHOOL for every existing row — matches the current
behaviour where every record is written by school staff.
"""
from alembic import op
import sqlalchemy as sa

revision = "20261009_000002"
down_revision = "20261009_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "attendance_records",
        sa.Column(
            "source",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'SCHOOL'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("attendance_records", "source")
