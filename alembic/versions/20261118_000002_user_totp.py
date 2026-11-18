"""Add optional TOTP 2FA fields to users.

- totp_secret: base32 secret the authenticator app stores. NULL
  when the user hasn't set up 2FA yet.
- totp_enabled: flipped to True only after the user verifies their
  first token (so a half-finished setup doesn't lock them out).
- totp_backup_codes: list of BCRYPT-HASHED backup codes. One-use
  each. Stored as JSONB so popping used codes is trivial.

PARENT role can enable 2FA but it defaults off for every role. Admins
and teachers get a Settings → Two-factor page; parents have the same
page from their Profile menu.

Revision ID: 20261118_000002
Revises: 20261118_000001
Create Date: 2026-11-18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261118_000002"
down_revision = "20261118_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("totp_secret", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "totp_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "totp_backup_codes",
            JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "totp_backup_codes")
    op.drop_column("users", "totp_enabled")
    op.drop_column("users", "totp_secret")
