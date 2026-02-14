"""Add subjects and grading systems tables

Revision ID: 20260214_000001
Revises: 20260213_000001
Create Date: 2026-02-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '20260214_000001'
down_revision: Union[str, None] = '20260213_000001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # === SUBJECTS ===
    op.create_table(
        'subjects',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('code', sa.String(length=20), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('default_total_marks', sa.Integer(), nullable=False, server_default='100'),
        sa.Column('category', sa.String(length=50), nullable=True),
        sa.Column('display_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_subjects_tenant', 'subjects', ['tenant_id'],
                    postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('idx_subjects_tenant_code', 'subjects', ['tenant_id', 'code'], unique=True,
                    postgresql_where=sa.text('deleted_at IS NULL'))

    # === CLASS SUBJECTS (Join table) ===
    op.create_table(
        'class_subjects',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('class_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('subject_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('total_marks', sa.Integer(), nullable=True),
        sa.Column('is_compulsory', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('display_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['class_id'], ['school_classes.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['subject_id'], ['subjects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_class_subjects_class', 'class_subjects', ['class_id'])
    op.create_index('idx_class_subjects_subject', 'class_subjects', ['subject_id'])
    op.create_index('idx_class_subjects_unique', 'class_subjects', ['class_id', 'subject_id'], unique=True)

    # === GRADING SYSTEMS ===
    op.create_table(
        'grading_systems',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('is_default', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('grades', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_grading_systems_tenant', 'grading_systems', ['tenant_id'],
                    postgresql_where=sa.text('deleted_at IS NULL'))

    # Add grading_system_id to report_templates
    op.add_column('report_templates',
                  sa.Column('grading_system_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key('fk_report_templates_grading_system',
                          'report_templates', 'grading_systems',
                          ['grading_system_id'], ['id'],
                          ondelete='SET NULL')


def downgrade() -> None:
    # Remove grading_system_id from report_templates
    op.drop_constraint('fk_report_templates_grading_system', 'report_templates', type_='foreignkey')
    op.drop_column('report_templates', 'grading_system_id')

    # Drop tables
    op.drop_index('idx_grading_systems_tenant', table_name='grading_systems')
    op.drop_table('grading_systems')

    op.drop_index('idx_class_subjects_unique', table_name='class_subjects')
    op.drop_index('idx_class_subjects_subject', table_name='class_subjects')
    op.drop_index('idx_class_subjects_class', table_name='class_subjects')
    op.drop_table('class_subjects')

    op.drop_index('idx_subjects_tenant_code', table_name='subjects')
    op.drop_index('idx_subjects_tenant', table_name='subjects')
    op.drop_table('subjects')
