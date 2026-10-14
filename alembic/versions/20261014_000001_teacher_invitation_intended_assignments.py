"""Add intended_assignments JSONB to teacher_invitations.

Revision ID: 20261014_000001
Revises: 20261012_000004
Create Date: 2026-10-14

Lets admins pre-plan a teacher's class + subject assignments at invite
time, so the moment the teacher accepts, their TeacherClass +
ClassSubjectTeacher rows are created automatically.

Each item in the list is a dict:
    {"class_id": "uuid",
     "is_primary": false,
     "subject_ids": ["uuid", ...]}

- Item with no subject_ids → general class teacher (TeacherClass row).
- Item with subject_ids → one ClassSubjectTeacher per subject.
- Both flags can coexist (hybrid: primary class teacher who's also a
  subject specialist elsewhere in the same class — rare but allowed).

Default is ``[]`` so every pre-feature invitation keeps the old shape
(no auto-assignment, admin wires it up manually after acceptance).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261014_000001"
down_revision = "20261012_000004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "teacher_invitations",
        sa.Column(
            "intended_assignments",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("teacher_invitations", "intended_assignments")
