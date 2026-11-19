"""Exams module — exams, exam_subjects (timetable), exam_results.

One exam per (class, term, year). Each exam auto-seeds an exam_subjects
row per subject the class takes (inherited from class_subjects). The
timetable lives on exam_subjects (scheduled_at + room). Marks land in
exam_results, and on finalize, the grade letter + percentage are
computed against the class's resolved grading system.

Revision ID: 20261119_000002
Revises: 20261119_000001
Create Date: 2026-11-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "20261119_000002"
down_revision = "20261119_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # exams
    op.create_table(
        "exams",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("class_id", UUID(as_uuid=True),
                  sa.ForeignKey("school_classes.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("academic_year", sa.String(10), nullable=False),  # "2026"
        sa.Column("term", sa.Integer(), nullable=False),            # 1..4
        sa.Column("status", sa.String(20), nullable=False,
                  server_default="DRAFT"),
        sa.Column("report_template_id", UUID(as_uuid=True),
                  sa.ForeignKey("report_templates.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("grading_system_id", UUID(as_uuid=True),
                  sa.ForeignKey("grading_systems.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_exams_tenant_class", "exams",
                    ["tenant_id", "class_id"])
    op.create_index("idx_exams_tenant_term", "exams",
                    ["tenant_id", "academic_year", "term"])
    op.create_unique_constraint(
        "uq_exams_tenant_class_year_term_name", "exams",
        ["tenant_id", "class_id", "academic_year", "term", "name"],
    )

    # exam_subjects — one row per (exam, subject). The timetable.
    op.create_table(
        "exam_subjects",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("exam_id", UUID(as_uuid=True),
                  sa.ForeignKey("exams.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("subject_id", UUID(as_uuid=True),
                  sa.ForeignKey("subjects.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("class_subject_id", UUID(as_uuid=True),
                  sa.ForeignKey("class_subjects.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("paper_name", sa.String(100), nullable=True),  # "Paper 1"
        sa.Column("total_marks", sa.Integer(), nullable=False),
        sa.Column("pass_mark", sa.Integer(), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("room", sa.String(100), nullable=True),
        sa.Column("invigilator_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("display_order", sa.Integer(),
                  nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("idx_exam_subjects_exam", "exam_subjects", ["exam_id"])
    op.create_index("idx_exam_subjects_scheduled", "exam_subjects",
                    ["tenant_id", "scheduled_at"])
    op.create_unique_constraint(
        "uq_exam_subjects_exam_subject", "exam_subjects",
        ["exam_id", "subject_id"],
    )

    # exam_results — per-student marks
    op.create_table(
        "exam_results",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("exam_subject_id", UUID(as_uuid=True),
                  sa.ForeignKey("exam_subjects.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("student_id", UUID(as_uuid=True),
                  sa.ForeignKey("students.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("marks_obtained", sa.Numeric(precision=6, scale=2),
                  nullable=True),
        sa.Column("percentage", sa.Numeric(precision=5, scale=2),
                  nullable=True),
        sa.Column("grade_letter", sa.String(10), nullable=True),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.Column("is_absent", sa.Boolean(),
                  nullable=False, server_default=sa.text("false")),
        sa.Column("marked_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("marked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("idx_exam_results_subject", "exam_results",
                    ["exam_subject_id"])
    op.create_index("idx_exam_results_student", "exam_results", ["student_id"])
    op.create_unique_constraint(
        "uq_exam_results_subject_student", "exam_results",
        ["exam_subject_id", "student_id"],
    )


def downgrade() -> None:
    op.drop_table("exam_results")
    op.drop_table("exam_subjects")
    op.drop_table("exams")
