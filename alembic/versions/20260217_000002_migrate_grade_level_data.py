"""Migrate existing grade level data to use FK relationships.

Revision ID: 20260217_000002
Revises: 20260217_000001
Create Date: 2026-02-17 12:00:00.000000

This migration:
1. Seeds default grade levels for each existing tenant based on education_type
2. Maps existing school_classes.age_group/grade_level strings to GradeLevel FKs
3. Migrates report_templates.applies_to_grade_level CSV to many-to-many join table
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import text
import uuid

# revision identifiers, used by Alembic.
revision = '20260217_000002'
down_revision = '20260217_000001'
branch_labels = None
depends_on = None


# Default grade levels by education type (matching grade_level_service.py)
DEFAULT_GRADE_LEVELS = {
    "DAYCARE": [
        {"code": "INFANT", "name": "Infant", "display_order": 1},
        {"code": "TODDLER", "name": "Toddler", "display_order": 2},
        {"code": "PRESCHOOL", "name": "Preschool", "display_order": 3},
        {"code": "KINDERGARTEN", "name": "Kindergarten", "display_order": 4},
    ],
    "PRIMARY_SCHOOL": [
        {"code": "GRADE_R", "name": "Grade R", "display_order": 1},
        {"code": "GRADE_1", "name": "Grade 1", "display_order": 2},
        {"code": "GRADE_2", "name": "Grade 2", "display_order": 3},
        {"code": "GRADE_3", "name": "Grade 3", "display_order": 4},
        {"code": "GRADE_4", "name": "Grade 4", "display_order": 5},
        {"code": "GRADE_5", "name": "Grade 5", "display_order": 6},
        {"code": "GRADE_6", "name": "Grade 6", "display_order": 7},
        {"code": "GRADE_7", "name": "Grade 7", "display_order": 8},
    ],
    "HIGH_SCHOOL": [
        {"code": "GRADE_8", "name": "Grade 8", "display_order": 1},
        {"code": "GRADE_9", "name": "Grade 9", "display_order": 2},
        {"code": "GRADE_10", "name": "Grade 10", "display_order": 3},
        {"code": "GRADE_11", "name": "Grade 11", "display_order": 4},
        {"code": "GRADE_12", "name": "Grade 12", "display_order": 5},
    ],
    "K12": [
        {"code": "GRADE_R", "name": "Grade R", "display_order": 1},
        {"code": "GRADE_1", "name": "Grade 1", "display_order": 2},
        {"code": "GRADE_2", "name": "Grade 2", "display_order": 3},
        {"code": "GRADE_3", "name": "Grade 3", "display_order": 4},
        {"code": "GRADE_4", "name": "Grade 4", "display_order": 5},
        {"code": "GRADE_5", "name": "Grade 5", "display_order": 6},
        {"code": "GRADE_6", "name": "Grade 6", "display_order": 7},
        {"code": "GRADE_7", "name": "Grade 7", "display_order": 8},
        {"code": "GRADE_8", "name": "Grade 8", "display_order": 9},
        {"code": "GRADE_9", "name": "Grade 9", "display_order": 10},
        {"code": "GRADE_10", "name": "Grade 10", "display_order": 11},
        {"code": "GRADE_11", "name": "Grade 11", "display_order": 12},
        {"code": "GRADE_12", "name": "Grade 12", "display_order": 13},
    ],
    "COMBINED": [
        {"code": "INFANT", "name": "Infant", "display_order": 1},
        {"code": "TODDLER", "name": "Toddler", "display_order": 2},
        {"code": "PRESCHOOL", "name": "Preschool", "display_order": 3},
        {"code": "KINDERGARTEN", "name": "Kindergarten", "display_order": 4},
        {"code": "GRADE_R", "name": "Grade R", "display_order": 5},
        {"code": "GRADE_1", "name": "Grade 1", "display_order": 6},
        {"code": "GRADE_2", "name": "Grade 2", "display_order": 7},
        {"code": "GRADE_3", "name": "Grade 3", "display_order": 8},
        {"code": "GRADE_4", "name": "Grade 4", "display_order": 9},
        {"code": "GRADE_5", "name": "Grade 5", "display_order": 10},
        {"code": "GRADE_6", "name": "Grade 6", "display_order": 11},
        {"code": "GRADE_7", "name": "Grade 7", "display_order": 12},
        {"code": "GRADE_8", "name": "Grade 8", "display_order": 13},
        {"code": "GRADE_9", "name": "Grade 9", "display_order": 14},
        {"code": "GRADE_10", "name": "Grade 10", "display_order": 15},
        {"code": "GRADE_11", "name": "Grade 11", "display_order": 16},
        {"code": "GRADE_12", "name": "Grade 12", "display_order": 17},
    ],
}


def normalize_grade_code(value: str) -> str:
    """Normalize a grade level string to a code format."""
    if not value:
        return ""
    # Remove spaces, convert to uppercase
    normalized = value.strip().upper().replace(" ", "_").replace("-", "_")
    # Handle common variations
    if normalized.startswith("GR_"):
        normalized = "GRADE_" + normalized[3:]
    if normalized.startswith("GR"):
        normalized = "GRADE_" + normalized[2:]
    return normalized


def upgrade() -> None:
    """Run data migration."""
    connection = op.get_bind()

    # Step 1: Get all tenants
    tenants = connection.execute(
        text("SELECT id, education_type FROM tenants WHERE deleted_at IS NULL")
    ).fetchall()

    for tenant in tenants:
        tenant_id = tenant[0]
        education_type = tenant[1] or "COMBINED"

        # Step 1a: Check if tenant already has grade levels
        existing_count = connection.execute(
            text("SELECT COUNT(*) FROM grade_levels WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id}
        ).scalar()

        if existing_count > 0:
            # Tenant already has grade levels, skip seeding
            continue

        # Step 1b: Seed default grade levels for this tenant
        grade_levels = DEFAULT_GRADE_LEVELS.get(education_type, DEFAULT_GRADE_LEVELS["COMBINED"])
        for gl in grade_levels:
            gl_id = str(uuid.uuid4())
            connection.execute(
                text("""
                    INSERT INTO grade_levels (id, tenant_id, name, code, display_order, is_active)
                    VALUES (:id, :tenant_id, :name, :code, :display_order, true)
                """),
                {
                    "id": gl_id,
                    "tenant_id": tenant_id,
                    "name": gl["name"],
                    "code": gl["code"],
                    "display_order": gl["display_order"],
                }
            )

    # Step 2: Get all grade levels as a lookup (tenant_id, code) -> grade_level_id
    all_grade_levels = connection.execute(
        text("SELECT id, tenant_id, code FROM grade_levels WHERE deleted_at IS NULL")
    ).fetchall()

    grade_level_lookup = {}
    for gl in all_grade_levels:
        key = (str(gl[1]), gl[2].upper())  # (tenant_id, code)
        grade_level_lookup[key] = str(gl[0])

    # Step 3: Migrate school_classes
    classes = connection.execute(
        text("""
            SELECT id, tenant_id, age_group, grade_level
            FROM school_classes
            WHERE deleted_at IS NULL
            AND grade_level_id IS NULL
            AND (age_group IS NOT NULL OR grade_level IS NOT NULL)
        """)
    ).fetchall()

    for cls in classes:
        class_id = cls[0]
        tenant_id = str(cls[1])
        age_group = cls[2]
        grade_level = cls[3]

        # Try to find matching grade level
        matched_gl_id = None

        # Try age_group first
        if age_group:
            code = normalize_grade_code(age_group)
            key = (tenant_id, code)
            if key in grade_level_lookup:
                matched_gl_id = grade_level_lookup[key]

        # If not found, try grade_level string
        if not matched_gl_id and grade_level:
            code = normalize_grade_code(grade_level)
            key = (tenant_id, code)
            if key in grade_level_lookup:
                matched_gl_id = grade_level_lookup[key]

        # Update class if we found a match
        if matched_gl_id:
            connection.execute(
                text("UPDATE school_classes SET grade_level_id = :gl_id WHERE id = :class_id"),
                {"gl_id": matched_gl_id, "class_id": class_id}
            )

    # Step 4: Migrate report_templates applies_to_grade_level to many-to-many
    templates = connection.execute(
        text("""
            SELECT id, tenant_id, applies_to_grade_level
            FROM report_templates
            WHERE deleted_at IS NULL
            AND applies_to_grade_level IS NOT NULL
            AND applies_to_grade_level != ''
        """)
    ).fetchall()

    for template in templates:
        template_id = str(template[0])
        tenant_id = str(template[1])
        applies_to = template[2]

        # Parse comma-separated values
        if not applies_to:
            continue

        grade_values = [v.strip().upper() for v in applies_to.split(",") if v.strip()]

        for value in grade_values:
            code = normalize_grade_code(value)
            key = (tenant_id, code)

            if key in grade_level_lookup:
                gl_id = grade_level_lookup[key]

                # Check if association already exists
                existing = connection.execute(
                    text("""
                        SELECT id FROM report_template_grade_levels
                        WHERE template_id = :template_id AND grade_level_id = :gl_id
                    """),
                    {"template_id": template_id, "gl_id": gl_id}
                ).fetchone()

                if not existing:
                    connection.execute(
                        text("""
                            INSERT INTO report_template_grade_levels (id, template_id, grade_level_id)
                            VALUES (:id, :template_id, :gl_id)
                        """),
                        {"id": str(uuid.uuid4()), "template_id": template_id, "gl_id": gl_id}
                    )


def downgrade() -> None:
    """Reverse the data migration (remove seeded grade levels and associations).

    Note: This is a destructive operation and will remove all grade levels
    and template associations that were created by this migration.
    """
    connection = op.get_bind()

    # Remove all report_template_grade_levels associations
    connection.execute(text("DELETE FROM report_template_grade_levels"))

    # Reset school_classes grade_level_id
    connection.execute(text("UPDATE school_classes SET grade_level_id = NULL"))

    # Remove all grade levels (this may fail if there are other FK references)
    connection.execute(text("DELETE FROM grade_levels"))
