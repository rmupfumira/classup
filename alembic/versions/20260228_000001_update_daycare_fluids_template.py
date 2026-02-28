"""update daycare fluids template section

Revision ID: c7e2f1a3b4d5
Revises: b501a59145b6
Create Date: 2026-02-28 00:00:01.000000

Data migration: Updates the fluids section in existing Daycare Daily Report
templates to use descriptive SELECT amounts instead of numeric, and adds
a notes field.
"""
from typing import Sequence, Union
import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = 'c7e2f1a3b4d5'
down_revision: Union[str, None] = 'b501a59145b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# New fluids section definition
NEW_FLUIDS_SECTION = {
    "id": "fluids",
    "title": "Fluids",
    "type": "REPEATABLE_ENTRIES",
    "color": "blue",
    "display_order": 3,
    "fields": [
        {"id": "time", "label": "Time", "type": "TIME", "required": True},
        {"id": "amount", "label": "Amount", "type": "SELECT", "required": False,
         "options": ["None", "Some", "Half", "Most", "All"]},
        {"id": "type", "label": "Type", "type": "SELECT", "required": True,
         "options": ["Water", "Milk", "Juice", "Formula", "Other"]},
        {"id": "notes", "label": "Notes", "type": "TEXT", "required": False}
    ]
}

# Old fluids section definition (for downgrade)
OLD_FLUIDS_SECTION = {
    "id": "fluids",
    "title": "Fluids & Hydration",
    "type": "REPEATABLE_ENTRIES",
    "color": "blue",
    "display_order": 3,
    "fields": [
        {"id": "time", "label": "Time", "type": "TIME", "required": True},
        {"id": "amount", "label": "Amount (ml)", "type": "NUMBER", "required": True},
        {"id": "type", "label": "Type", "type": "SELECT", "required": True,
         "options": ["Water", "Milk", "Juice", "Formula"]}
    ]
}


def upgrade() -> None:
    conn = op.get_bind()

    # Find all daycare daily report templates with a fluids section
    results = conn.execute(
        sa.text(
            "SELECT id, sections FROM report_templates "
            "WHERE report_type = 'DAILY_ACTIVITY' "
            "AND deleted_at IS NULL "
            "AND sections::text LIKE '%fluids%'"
        )
    ).fetchall()

    for row in results:
        template_id = row[0]
        sections = row[1] if isinstance(row[1], list) else json.loads(row[1])

        updated = False
        for i, section in enumerate(sections):
            if section.get("id") == "fluids":
                sections[i] = NEW_FLUIDS_SECTION
                updated = True
                break

        if updated:
            conn.execute(
                sa.text(
                    "UPDATE report_templates SET sections = :sections, "
                    "updated_at = now() WHERE id = :id"
                ),
                {"sections": json.dumps(sections), "id": template_id}
            )


def downgrade() -> None:
    conn = op.get_bind()

    results = conn.execute(
        sa.text(
            "SELECT id, sections FROM report_templates "
            "WHERE report_type = 'DAILY_ACTIVITY' "
            "AND deleted_at IS NULL "
            "AND sections::text LIKE '%fluids%'"
        )
    ).fetchall()

    for row in results:
        template_id = row[0]
        sections = row[1] if isinstance(row[1], list) else json.loads(row[1])

        updated = False
        for i, section in enumerate(sections):
            if section.get("id") == "fluids":
                sections[i] = OLD_FLUIDS_SECTION
                updated = True
                break

        if updated:
            conn.execute(
                sa.text(
                    "UPDATE report_templates SET sections = :sections, "
                    "updated_at = now() WHERE id = :id"
                ),
                {"sections": json.dumps(sections), "id": template_id}
            )
