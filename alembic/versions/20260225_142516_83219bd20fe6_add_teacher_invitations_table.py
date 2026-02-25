"""Add teacher_invitations table

Revision ID: 83219bd20fe6
Revises: 20260217_000002
Create Date: 2026-02-25 14:25:16.030424

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '83219bd20fe6'
down_revision: Union[str, None] = '20260217_000002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('teacher_invitations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('first_name', sa.String(length=100), nullable=False),
    sa.Column('last_name', sa.String(length=100), nullable=False),
    sa.Column('invitation_code', sa.String(length=8), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('invitation_code')
    )
    op.create_index('idx_teacher_invitations_code', 'teacher_invitations', ['invitation_code'], unique=False, postgresql_where=sa.text("status = 'PENDING'"))
    op.create_index('idx_teacher_invitations_email', 'teacher_invitations', ['email', 'tenant_id'], unique=False, postgresql_where=sa.text("status = 'PENDING'"))
    op.create_index(op.f('ix_teacher_invitations_tenant_id'), 'teacher_invitations', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_teacher_invitations_tenant_id'), table_name='teacher_invitations')
    op.drop_index('idx_teacher_invitations_email', table_name='teacher_invitations', postgresql_where=sa.text("status = 'PENDING'"))
    op.drop_index('idx_teacher_invitations_code', table_name='teacher_invitations', postgresql_where=sa.text("status = 'PENDING'"))
    op.drop_table('teacher_invitations')
