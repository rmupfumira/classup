"""Add grade_level_subjects table.

Revision ID: 20261015_000001
Revises: 20261014_000001
Create Date: 2026-10-15

Anchors subjects at the GRADE LEVEL instead of per class. "Mathematics
is a Form 4 subject" is said once, and every current + future Form 4
class auto-attaches it via ``class_subjects`` on create. Removing a
subject from a grade level cascades (via the service, not the FK) to
every class in that grade.

Many-to-many: a subject can be linked to multiple grade levels
(English → every grade), and a grade level has many subjects.
"""
from alembic import op
import sqlalchemy as sa


revision = "20261015_000001"
down_revision = "20261014_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "grade_level_subjects",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "grade_level_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grade_levels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subject_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("subjects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "display_order",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "idx_grade_level_subjects_grade",
        "grade_level_subjects",
        ["grade_level_id"],
    )
    op.create_index(
        "idx_grade_level_subjects_subject",
        "grade_level_subjects",
        ["subject_id"],
    )
    op.create_index(
        "idx_grade_level_subjects_unique",
        "grade_level_subjects",
        ["grade_level_id", "subject_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_grade_level_subjects_unique", table_name="grade_level_subjects",
    )
    op.drop_index(
        "idx_grade_level_subjects_subject", table_name="grade_level_subjects",
    )
    op.drop_index(
        "idx_grade_level_subjects_grade", table_name="grade_level_subjects",
    )
    op.drop_table("grade_level_subjects")
