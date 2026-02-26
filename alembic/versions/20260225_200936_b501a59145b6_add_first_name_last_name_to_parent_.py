"""add first_name last_name to parent_invitations

Revision ID: b501a59145b6
Revises: 8a3d0c306244
Create Date: 2026-02-25 20:09:36.741871

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b501a59145b6'
down_revision: Union[str, None] = '8a3d0c306244'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'parent_invitations',
        sa.Column('first_name', sa.String(length=100), nullable=False, server_default=''),
    )
    op.add_column(
        'parent_invitations',
        sa.Column('last_name', sa.String(length=100), nullable=False, server_default=''),
    )


def downgrade() -> None:
    op.drop_column('parent_invitations', 'last_name')
    op.drop_column('parent_invitations', 'first_name')
