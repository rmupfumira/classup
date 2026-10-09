"""Add class grading system FK + subject curriculum pack code.

Revision ID: 20261009_000003
Revises: 20261009_000002
Create Date: 2026-10-09

Two linked columns that let a class inherit the grading scale from
whichever curriculum its mapped subjects came from, while leaving the
school admin free to override:

- ``school_classes.grading_system_id`` (nullable FK) — the per-class
  grading scale. When NULL the tenant-wide default (``is_default=True``
  on ``grading_systems``) is used instead. Set automatically the first
  time subjects are mapped to the class (service infers from the most-
  used curriculum among those subjects); the school admin can change
  it at any time from the class edit page.

- ``subjects.curriculum_pack_code`` (nullable) — stamps each seeded
  subject with its origin pack (ZW_ZIMSEC / ZA_CAPS / ZA_IEB /
  ZW_CAMBRIDGE / ZA_CAMBRIDGE). Powers the "infer from subjects"
  mechanism above and makes the admin UI able to show "this subject
  came from ZIMSEC". Subjects created manually or imported before this
  migration stay NULL — the inferrer treats them as "no opinion" and
  leans on the other subjects' codes.

Both are nullable so existing data lives untouched. The matching
server-side ``ON DELETE SET NULL`` on the class FK means removing a
grading system doesn't orphan the classes using it.
"""
from alembic import op
import sqlalchemy as sa

revision = "20261009_000003"
down_revision = "20261009_000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # School class → active grading system (nullable override)
    op.add_column(
        "school_classes",
        sa.Column("grading_system_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_school_classes_grading_system",
        source_table="school_classes",
        referent_table="grading_systems",
        local_cols=["grading_system_id"],
        remote_cols=["id"],
        ondelete="SET NULL",
    )

    # Subject → curriculum pack code (stamp of origin)
    op.add_column(
        "subjects",
        sa.Column("curriculum_pack_code", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "idx_subjects_tenant_pack",
        "subjects",
        ["tenant_id", "curriculum_pack_code"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_subjects_tenant_pack", table_name="subjects")
    op.drop_column("subjects", "curriculum_pack_code")
    op.drop_constraint(
        "fk_school_classes_grading_system",
        "school_classes",
        type_="foreignkey",
    )
    op.drop_column("school_classes", "grading_system_id")
