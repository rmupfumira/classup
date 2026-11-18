"""Add granular notification preferences on User.

Shape of `notification_prefs` JSONB (default {} = all on):

    {
      "attendance":    {"email": true, "whatsapp": true},
      "announcements": {"email": true, "whatsapp": true},
      "invoices":      {"email": true, "whatsapp": true},
      "photos":        {"email": true, "whatsapp": true},
      "documents":     {"email": true, "whatsapp": true},
      "events":        {"email": true, "whatsapp": true},
      "messages":      {"email": true, "whatsapp": true},
      "reports":       {"email": true, "whatsapp": true}
    }

A missing event or missing channel in the dict means "on" — this way
every existing user gets all notifications until they explicitly turn
one off. `User.email_opted_in` and `User.whatsapp_opted_in` are the
master toggles that override any per-event setting.

Revision ID: 20261118_000001
Revises: 20261117_000001
Create Date: 2026-11-18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261118_000001"
down_revision = "20261117_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "notification_prefs",
            JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "notification_prefs")
