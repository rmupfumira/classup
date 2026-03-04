"""Add document_shares, document_share_files, and document_share_tags tables

Revision ID: 20260304_000003
Revises: 20260304_000002
Create Date: 2026-03-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260304_000003"
down_revision: Union[str, None] = "20260304_000002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_shares",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("class_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("shared_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["class_id"], ["school_classes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["shared_by"], ["users.id"], ondelete="CASCADE"),
    )

    op.create_index("ix_document_shares_tenant_id", "document_shares", ["tenant_id"])
    op.create_index(
        "idx_document_shares_tenant_scope",
        "document_shares",
        ["tenant_id", "scope"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "document_share_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_share_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["document_share_id"], ["document_shares.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["file_entity_id"], ["file_entities.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("document_share_id", "file_entity_id", name="uq_document_share_file"),
    )

    op.create_table(
        "document_share_tags",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_share_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("student_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["document_share_id"], ["document_shares.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("document_share_id", "student_id", name="uq_document_share_tag"),
    )


def downgrade() -> None:
    op.drop_table("document_share_tags")
    op.drop_table("document_share_files")
    op.drop_index("idx_document_shares_tenant_scope", table_name="document_shares")
    op.drop_index("ix_document_shares_tenant_id", table_name="document_shares")
    op.drop_table("document_shares")
