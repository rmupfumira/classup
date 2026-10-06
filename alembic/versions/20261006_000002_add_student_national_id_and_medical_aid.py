"""Add student national_id + medical_aid columns.

Revision ID: 20261006_000002
Revises: 20261006_000001
Create Date: 2026-10-06

Owner directives 2026-10-06:

- Capture an optional national ID per student. Not required, not
  validated (varies by jurisdiction and the admin shouldn't be blocked
  by a format mismatch). Free-form string, VARCHAR(50) comfortably
  holds every country's ID.

- Record medical-aid details per student (JSONB). Nullable so NULL
  means "nothing captured yet", distinct from
  ``{"has_medical_aid": false}``.  Expected shape::

    {"has_medical_aid": bool, "name": str, "number": str,
     "currency": str (ISO 4217), "package": str}

  Keeping it JSONB lets us grow the shape later (e.g. principal member
  name, main-member relation, policy expiry) without column churn.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261006_000002"
down_revision = "20261006_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "students",
        sa.Column("national_id", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "students",
        sa.Column("medical_aid", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("students", "medical_aid")
    op.drop_column("students", "national_id")
