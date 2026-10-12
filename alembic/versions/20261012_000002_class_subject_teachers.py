"""Add class_subject_teachers table.

Revision ID: 20261012_000002
Revises: 20261012_000001
Create Date: 2026-10-12

Links a teacher to a specific subject within a specific class — the
secondary-school pattern where each subject has its own specialist.
Only active when ``tenant.settings.subject_teacher_enabled`` is True;
otherwise ignored and the general class teacher continues to handle
every subject (primary-school default behaviour).
"""
from alembic import op
import sqlalchemy as sa

revision = "20261012_000002"
down_revision = "20261012_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "class_subject_teachers",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "class_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("school_classes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subject_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("subjects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "teacher_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
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
    op.create_index("idx_cst_class", "class_subject_teachers", ["class_id"])
    op.create_index("idx_cst_subject", "class_subject_teachers", ["subject_id"])
    op.create_index("idx_cst_teacher", "class_subject_teachers", ["teacher_id"])
    op.create_index(
        "idx_cst_unique",
        "class_subject_teachers",
        ["class_id", "subject_id", "teacher_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("idx_cst_unique", table_name="class_subject_teachers")
    op.drop_index("idx_cst_teacher", table_name="class_subject_teachers")
    op.drop_index("idx_cst_subject", table_name="class_subject_teachers")
    op.drop_index("idx_cst_class", table_name="class_subject_teachers")
    op.drop_table("class_subject_teachers")
