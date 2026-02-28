"""Update nap duration field type from TEXT to CALCULATED in report templates.

Revision ID: 20260228_000002
Revises: 20260228_000001
Create Date: 2026-02-28

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "20260228_000002"
down_revision = "20260228_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Update existing report templates: change nap duration field type from TEXT to CALCULATED."""
    conn = op.get_bind()

    # Find all report templates that have a naps section with a duration field of type TEXT
    result = conn.execute(
        sa.text("""
            SELECT id, sections FROM report_templates
            WHERE sections::text LIKE '%"naps"%'
            AND sections::text LIKE '%"duration"%'
            AND deleted_at IS NULL
        """)
    )

    for row in result:
        template_id = row[0]
        sections = row[1]

        updated = False
        for section in sections:
            if section.get("id") == "naps":
                for field in section.get("fields", []):
                    if field.get("id") == "duration" and field.get("type") == "TEXT":
                        field["type"] = "CALCULATED"
                        updated = True

        if updated:
            import json
            conn.execute(
                sa.text("UPDATE report_templates SET sections = :sections WHERE id = :id"),
                {"sections": json.dumps(sections), "id": template_id},
            )


def downgrade() -> None:
    """Revert nap duration field type from CALCULATED back to TEXT."""
    conn = op.get_bind()

    result = conn.execute(
        sa.text("""
            SELECT id, sections FROM report_templates
            WHERE sections::text LIKE '%"naps"%'
            AND sections::text LIKE '%"duration"%'
            AND deleted_at IS NULL
        """)
    )

    for row in result:
        template_id = row[0]
        sections = row[1]

        updated = False
        for section in sections:
            if section.get("id") == "naps":
                for field in section.get("fields", []):
                    if field.get("id") == "duration" and field.get("type") == "CALCULATED":
                        field["type"] = "TEXT"
                        updated = True

        if updated:
            import json
            conn.execute(
                sa.text("UPDATE report_templates SET sections = :sections WHERE id = :id"),
                {"sections": json.dumps(sections), "id": template_id},
            )
