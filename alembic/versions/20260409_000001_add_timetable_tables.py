"""Add timetable tables (timetable_configs, timetables, timetable_entries)

Revision ID: 20260409_000001
Revises: 20260318_110056
Create Date: 2026-04-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "20260409_000001"
down_revision: Union[str, None] = "05d60405a4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- timetable_configs ---
    op.create_table(
        "timetable_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("days", postgresql.JSONB(), nullable=False),
        sa.Column("periods", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", name="uq_timetable_config_tenant"),
    )
    op.create_index("ix_timetable_configs_tenant_id", "timetable_configs", ["tenant_id"])

    # --- timetables ---
    op.create_table(
        "timetables",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("class_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["class_id"], ["school_classes.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_timetables_tenant_id", "timetables", ["tenant_id"])
    op.create_index("idx_timetables_tenant", "timetables", ["tenant_id"])
    op.create_index(
        "idx_timetables_active_per_class",
        "timetables",
        ["class_id"],
        unique=True,
        postgresql_where=sa.text("is_active = true AND deleted_at IS NULL"),
    )

    # --- timetable_entries ---
    op.create_table(
        "timetable_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timetable_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("day", sa.String(length=3), nullable=False),
        sa.Column("period_index", sa.Integer(), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("teacher_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["timetable_id"], ["timetables.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["teacher_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("timetable_id", "day", "period_index", name="uq_timetable_entry_slot"),
    )
    op.create_index("ix_timetable_entries_tenant_id", "timetable_entries", ["tenant_id"])
    op.create_index("idx_timetable_entries_timetable", "timetable_entries", ["timetable_id"])
    op.create_index(
        "idx_timetable_entries_teacher_slot",
        "timetable_entries",
        ["tenant_id", "teacher_id", "day", "period_index"],
    )


def downgrade() -> None:
    op.drop_index("idx_timetable_entries_teacher_slot", table_name="timetable_entries")
    op.drop_index("idx_timetable_entries_timetable", table_name="timetable_entries")
    op.drop_index("ix_timetable_entries_tenant_id", table_name="timetable_entries")
    op.drop_table("timetable_entries")

    op.drop_index("idx_timetables_active_per_class", table_name="timetables")
    op.drop_index("idx_timetables_tenant", table_name="timetables")
    op.drop_index("ix_timetables_tenant_id", table_name="timetables")
    op.drop_table("timetables")

    op.drop_index("ix_timetable_configs_tenant_id", table_name="timetable_configs")
    op.drop_table("timetable_configs")
