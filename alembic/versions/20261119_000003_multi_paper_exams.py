"""Multiple papers per subject within an exam.

ZIMSEC / Cambridge subjects routinely have 2-3 papers (English
Composition + Comprehension; Combined Science Paper 1/2/3 with
Practical) that aggregate to one subject mark via weighted average.

Changes:
  - exam_subjects.weight_percent — int 0..100, default 100
  - Drop UNIQUE(exam_id, subject_id) so multiple papers per subject
    are allowed. The UI uses paper_name to distinguish them.

Backfill: existing rows get weight_percent=100, matching the
single-paper-per-subject behaviour the v1 code assumed.

Revision ID: 20261119_000003
Revises: 20261119_000002
Create Date: 2026-11-19
"""

from alembic import op
import sqlalchemy as sa


revision = "20261119_000003"
down_revision = "20261119_000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "exam_subjects",
        sa.Column(
            "weight_percent",
            sa.Integer(),
            nullable=False,
            server_default="100",
        ),
    )
    # Drop the unique constraint so a subject can have multiple papers
    op.drop_constraint(
        "uq_exam_subjects_exam_subject", "exam_subjects", type_="unique",
    )


def downgrade() -> None:
    op.create_unique_constraint(
        "uq_exam_subjects_exam_subject", "exam_subjects",
        ["exam_id", "subject_id"],
    )
    op.drop_column("exam_subjects", "weight_percent")
