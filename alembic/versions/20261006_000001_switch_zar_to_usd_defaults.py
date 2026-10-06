"""Switch ZAR defaults to USD across the platform.

Revision ID: 20261006_000001
Revises: 20260912_000001
Create Date: 2026-10-06

Owner directive 2026-10-06: Rands removed completely from the system —
USD is now the universal default. This migration:

- Flips every ``server_default = 'ZAR'`` to ``'USD'`` on currency
  columns (subscription_plans.currency, platform_invoices.currency,
  platform_eft_payments.currency, bank_accounts.currency,
  accounting_transactions.currency).
- Updates existing rows whose ``currency = 'ZAR'`` to ``'USD'`` so the
  app presents a consistent currency to every operator.
- Overwrites every ``tenant.settings.billing_currency = 'ZAR'`` to
  ``'USD'`` (JSONB update).
- Overwrites the ``system_settings.platform_defaults.default_currency``
  blob when it is currently ``'ZAR'``.

The ``subscription_plans`` catalogue carries the plans in Rands today,
so this migration will convert them to USD on paper — the price amounts
are left alone (owner directive is a straight currency re-label, not
an FX conversion). If prices need to be re-stated in actual USD, that's
a separate super-admin step.
"""
from alembic import op
import sqlalchemy as sa

revision = "20261006_000001"
down_revision = "20260912_000001"
branch_labels = None
depends_on = None


# Every column where server_default is "ZAR" and we want to flip to
# "USD" + rewrite existing ZAR rows.
_CURRENCY_COLUMNS = [
    ("subscription_plans", "currency"),
    ("platform_invoices", "currency"),
    ("platform_eft_payments", "currency"),
    ("bank_accounts", "currency"),
    ("accounting_transactions", "currency"),
]


def upgrade() -> None:
    for table, col in _CURRENCY_COLUMNS:
        # Flip the server default so new INSERTs that omit currency get
        # USD not ZAR going forward.
        op.alter_column(
            table, col,
            existing_type=sa.String(length=3),
            existing_nullable=False,
            server_default=sa.text("'USD'"),
        )
        # Rewrite existing rows holding ZAR.
        op.execute(sa.text(f"UPDATE {table} SET {col} = 'USD' WHERE {col} = 'ZAR'"))

    # Tenants carry billing_currency inside the settings JSONB. jsonb_set
    # only replaces if the key already matches a sentinel; just overwrite
    # the whole key when it is ZAR.
    op.execute(sa.text(
        "UPDATE tenants "
        "SET settings = jsonb_set(settings, '{billing_currency}', '\"USD\"') "
        "WHERE settings->>'billing_currency' = 'ZAR'"
    ))

    # Platform defaults (singleton system_settings row). Overwrite the
    # default_currency key if it reads ZAR. Also fix default_country +
    # default_timezone so the platform default matches a USD-using
    # jurisdiction (Zimbabwe) instead of a ZAR-using one.
    op.execute(sa.text(
        "UPDATE system_settings "
        "SET value = jsonb_set(value, '{default_currency}', '\"USD\"') "
        "WHERE key = 'platform_defaults' "
        "AND value->>'default_currency' = 'ZAR'"
    ))
    op.execute(sa.text(
        "UPDATE system_settings "
        "SET value = jsonb_set(value, '{default_country}', '\"ZW\"') "
        "WHERE key = 'platform_defaults' "
        "AND value->>'default_country' = 'ZA'"
    ))
    op.execute(sa.text(
        "UPDATE system_settings "
        "SET value = jsonb_set(value, '{default_timezone}', '\"Africa/Harare\"') "
        "WHERE key = 'platform_defaults' "
        "AND value->>'default_timezone' = 'Africa/Johannesburg'"
    ))


def downgrade() -> None:
    for table, col in _CURRENCY_COLUMNS:
        op.alter_column(
            table, col,
            existing_type=sa.String(length=3),
            existing_nullable=False,
            server_default=sa.text("'ZAR'"),
        )
        # Note: we don't restore data on downgrade — that would need a
        # record of which rows were ZAR before the forward migration.
