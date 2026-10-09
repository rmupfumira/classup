"""Onboarding redesign: parent_invitations + users channel preference columns.

Revision ID: 20261009_000001
Revises: 20261008_000001
Create Date: 2026-10-09

Supports the parent onboarding redesign (2026-10-09):

- ``parent_invitations.parent_phone`` carries the mobile the admin
  typed when adding the student, so the registration page can
  pre-fill it instead of asking the parent to retype their own
  number. Optional on the invitation (admin may not know it yet);
  the registration page itself still requires one before account
  creation.
- ``parent_invitations.suggest_whatsapp_opt_in`` records whether
  the admin ticked "Suggest WhatsApp opt-in" when sending the
  invite. The registration page uses this to pre-tick the parent's
  WhatsApp opt-in checkbox — the parent still confirms or unticks
  it themselves (POPIA/GDPR affirmative consent). Default false so
  existing pending invitations without the flag behave like an
  email-only invite.
- ``users.email_opted_in`` adds a per-channel opt-out for email,
  matching the long-standing WhatsApp pattern. Default true (email
  is the baseline channel). A parent can turn it off from Profile;
  the matching gate in parent_notifier respects it on every send.
"""
from alembic import op
import sqlalchemy as sa

revision = "20261009_000001"
down_revision = "20261008_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "parent_invitations",
        sa.Column("parent_phone", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "parent_invitations",
        sa.Column(
            "suggest_whatsapp_opt_in",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "email_opted_in",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "email_opted_in")
    op.drop_column("parent_invitations", "suggest_whatsapp_opt_in")
    op.drop_column("parent_invitations", "parent_phone")
