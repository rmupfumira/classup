"""Add grade levels table and relationships

Revision ID: 20260217_000001
Revises: 20260214_000001
Create Date: 2026-02-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '20260217_000001'
down_revision: Union[str, None] = '20260214_000001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # === GRADE LEVELS ===
    op.create_table(
        'grade_levels',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('code', sa.String(length=50), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('display_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_grade_levels_tenant', 'grade_levels', ['tenant_id'],
                    postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('idx_grade_levels_tenant_code', 'grade_levels', ['tenant_id', 'code'], unique=True,
                    postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('idx_grade_levels_tenant_active', 'grade_levels', ['tenant_id'],
                    postgresql_where=sa.text('deleted_at IS NULL AND is_active = true'))

    # === REPORT TEMPLATE GRADE LEVELS (Join table) ===
    op.create_table(
        'report_template_grade_levels',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('template_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('grade_level_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['template_id'], ['report_templates.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['grade_level_id'], ['grade_levels.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_template_grade_levels_template', 'report_template_grade_levels', ['template_id'])
    op.create_index('idx_template_grade_levels_grade_level', 'report_template_grade_levels', ['grade_level_id'])
    op.create_index('idx_template_grade_levels_unique', 'report_template_grade_levels',
                    ['template_id', 'grade_level_id'], unique=True)

    # === ADD grade_level_id TO SCHOOL CLASSES ===
    op.add_column('school_classes',
                  sa.Column('grade_level_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key('fk_school_classes_grade_level',
                          'school_classes', 'grade_levels',
                          ['grade_level_id'], ['id'],
                          ondelete='SET NULL')
    op.create_index('idx_school_classes_grade_level', 'school_classes', ['grade_level_id'])


def downgrade() -> None:
    # Remove grade_level_id from school_classes
    op.drop_index('idx_school_classes_grade_level', table_name='school_classes')
    op.drop_constraint('fk_school_classes_grade_level', 'school_classes', type_='foreignkey')
    op.drop_column('school_classes', 'grade_level_id')

    # Drop report_template_grade_levels join table
    op.drop_index('idx_template_grade_levels_unique', table_name='report_template_grade_levels')
    op.drop_index('idx_template_grade_levels_grade_level', table_name='report_template_grade_levels')
    op.drop_index('idx_template_grade_levels_template', table_name='report_template_grade_levels')
    op.drop_table('report_template_grade_levels')

    # Drop grade_levels table
    op.drop_index('idx_grade_levels_tenant_active', table_name='grade_levels')
    op.drop_index('idx_grade_levels_tenant_code', table_name='grade_levels')
    op.drop_index('idx_grade_levels_tenant', table_name='grade_levels')
    op.drop_table('grade_levels')
