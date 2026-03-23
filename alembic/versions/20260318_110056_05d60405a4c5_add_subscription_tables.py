"""Add subscription tables

Revision ID: 05d60405a4c5
Revises: 20260306_000002
Create Date: 2026-03-18 11:00:56.024052

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '05d60405a4c5'
down_revision: Union[str, None] = '20260306_000002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('subscription_plans',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('price_monthly', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('price_annually', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('max_students', sa.Integer(), nullable=True),
    sa.Column('max_staff', sa.Integer(), nullable=True),
    sa.Column('features', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('trial_days', sa.Integer(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('display_order', sa.Integer(), nullable=False),
    sa.Column('paystack_plan_code', sa.String(length=100), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_subscription_plans_active', 'subscription_plans', ['is_active'], unique=False, postgresql_where=sa.text('deleted_at IS NULL'))

    op.create_table('tenant_subscriptions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('plan_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('trial_start', sa.Date(), nullable=True),
    sa.Column('trial_end', sa.Date(), nullable=True),
    sa.Column('current_period_start', sa.Date(), nullable=True),
    sa.Column('current_period_end', sa.Date(), nullable=True),
    sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('paystack_customer_code', sa.String(length=100), nullable=True),
    sa.Column('paystack_subscription_code', sa.String(length=100), nullable=True),
    sa.Column('paystack_email_token', sa.String(length=100), nullable=True),
    sa.Column('paystack_authorization_code', sa.String(length=200), nullable=True),
    sa.Column('grace_period_end', sa.Date(), nullable=True),
    sa.Column('failed_payment_count', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['plan_id'], ['subscription_plans.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_tenant_subscriptions_status', 'tenant_subscriptions', ['tenant_id', 'status'], unique=False)
    op.create_index('idx_tenant_subscriptions_tenant', 'tenant_subscriptions', ['tenant_id'], unique=False)

    op.create_table('platform_invoices',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('subscription_id', sa.UUID(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('billing_period_start', sa.Date(), nullable=False),
    sa.Column('billing_period_end', sa.Date(), nullable=False),
    sa.Column('paystack_reference', sa.String(length=200), nullable=True),
    sa.Column('paystack_transaction_id', sa.String(length=100), nullable=True),
    sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('payment_method', sa.String(length=50), nullable=True),
    sa.Column('failure_reason', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['subscription_id'], ['tenant_subscriptions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_platform_invoices_subscription', 'platform_invoices', ['subscription_id'], unique=False)
    op.create_index('idx_platform_invoices_tenant', 'platform_invoices', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_platform_invoices_tenant', table_name='platform_invoices')
    op.drop_index('idx_platform_invoices_subscription', table_name='platform_invoices')
    op.drop_table('platform_invoices')
    op.drop_index('idx_tenant_subscriptions_tenant', table_name='tenant_subscriptions')
    op.drop_index('idx_tenant_subscriptions_status', table_name='tenant_subscriptions')
    op.drop_table('tenant_subscriptions')
    op.drop_index('idx_subscription_plans_active', table_name='subscription_plans', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_table('subscription_plans')
