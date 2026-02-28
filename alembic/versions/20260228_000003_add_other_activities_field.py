"""Add 'Other Activities' textarea field to activities section in report templates.

Revision ID: 20260228_000003
Revises: 20260228_000002
Create Date: 2026-02-28

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "20260228_000003"
down_revision = "20260228_000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add 'other_activities' TEXTAREA field to activities CHECKLIST sections in existing templates."""
    conn = op.get_bind()

    # Find all report templates that have an activities section
    result = conn.execute(
        sa.text("""
            SELECT id, sections FROM report_templates
            WHERE sections::text LIKE '%"activities"%'
            AND sections::text LIKE '%"CHECKLIST"%'
            AND deleted_at IS NULL
        """)
    )

    for row in result:
        template_id = row[0]
        sections = row[1]

        updated = False
        for section in sections:
            if section.get("id") == "activities" and section.get("type") == "CHECKLIST":
                fields = section.get("fields", [])
                # Check if other_activities field already exists
                has_other = any(f.get("id") == "other_activities" for f in fields)
                if not has_other:
                    fields.append({
                        "id": "other_activities",
                        "label": "Other Activities",
                        "type": "TEXTAREA",
                        "required": False,
                    })
                    updated = True

        if updated:
            import json
            conn.execute(
                sa.text("UPDATE report_templates SET sections = :sections WHERE id = :id"),
                {"sections": json.dumps(sections), "id": template_id},
            )


def downgrade() -> None:
    """Remove 'other_activities' field from activities sections in existing templates."""
    conn = op.get_bind()

    result = conn.execute(
        sa.text("""
            SELECT id, sections FROM report_templates
            WHERE sections::text LIKE '%"other_activities"%'
            AND deleted_at IS NULL
        """)
    )

    for row in result:
        template_id = row[0]
        sections = row[1]

        updated = False
        for section in sections:
            if section.get("id") == "activities" and section.get("type") == "CHECKLIST":
                fields = section.get("fields", [])
                original_len = len(fields)
                section["fields"] = [f for f in fields if f.get("id") != "other_activities"]
                if len(section["fields"]) < original_len:
                    updated = True

        if updated:
            import json
            conn.execute(
                sa.text("UPDATE report_templates SET sections = :sections WHERE id = :id"),
                {"sections": json.dumps(sections), "id": template_id},
            )
