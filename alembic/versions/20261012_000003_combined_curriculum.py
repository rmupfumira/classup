"""Combined ZIMSEC+Cambridge curriculum: curriculum_track + overrides.

Revision ID: 20261012_000003
Revises: 20261012_000002
Create Date: 2026-10-12

Adds two things for the Combined-curriculum workflow (task #158):

1. ``school_classes.curriculum_track`` — nullable String(32).
   Values are "ZIMSEC" / "CAMBRIDGE" / NULL (inherit from tenant).
   Hints the grading-system inferrer; does not restrict which
   subjects can be assigned.

2. ``student_subject_overrides`` — per-student swap of a specific
   ``class_subjects`` row for a different ``subjects`` row. Used
   when an individual student follows a different syllabus for one
   or more subjects than their class default.
"""
from alembic import op
import sqlalchemy as sa

revision = "20261012_000003"
down_revision = "20261012_000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "school_classes",
        sa.Column("curriculum_track", sa.String(length=32), nullable=True),
    )

    op.create_table(
        "student_subject_overrides",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "student_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("students.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "class_subject_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("class_subjects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "override_subject_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("subjects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "idx_sso_student", "student_subject_overrides", ["student_id"],
    )
    op.create_index(
        "idx_sso_class_subject",
        "student_subject_overrides",
        ["class_subject_id"],
    )
    op.create_index(
        "idx_sso_unique",
        "student_subject_overrides",
        ["student_id", "class_subject_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("idx_sso_unique", table_name="student_subject_overrides")
    op.drop_index("idx_sso_class_subject", table_name="student_subject_overrides")
    op.drop_index("idx_sso_student", table_name="student_subject_overrides")
    op.drop_table("student_subject_overrides")
    op.drop_column("school_classes", "curriculum_track")
