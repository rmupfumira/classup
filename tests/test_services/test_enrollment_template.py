"""Tests for the enrollment template service (2026-11-18 fixes):

- Phone columns ship as text format so Excel doesn't convert
  "+263771234567" into scientific notation.
- Header rows 1-2 are locked under sheet protection, data rows stay
  editable.
- Parent columns include first/last name so the bulk importer can
  actually invite them.
- parse_excel defensively converts a phone that landed in Excel as a
  float (an old template without the text fix) back into a digit
  string with no exponent.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from openpyxl import load_workbook

from app.services.enrollment_template_service import (
    ENROLLMENT_COLUMNS,
    TEXT_FORMAT_FIELDS,
    EnrollmentTemplateService,
)


def test_parent_name_columns_included():
    """Bulk import needs parent_first_name + parent_last_name so the
    import row handler can build a ParentEnrollmentInfo with required
    fields. Older template had only email + phone."""
    field_names = [col[0] for col in ENROLLMENT_COLUMNS]
    assert "parent_first_name" in field_names
    assert "parent_last_name" in field_names
    assert "parent_email" in field_names
    assert "parent_phone" in field_names


def test_phone_columns_are_text_formatted():
    """Phone + date columns in the GENERATED template must have
    number_format='@' on data rows so Excel treats long digit strings
    as text."""
    svc = EnrollmentTemplateService()
    xlsx_bytes = svc.generate_template(class_names=["Grade 1"])
    wb = load_workbook(BytesIO(xlsx_bytes))
    ws = wb.active

    # Find the column index for parent_phone.
    phone_col = None
    for idx, (field_name, _, _, _) in enumerate(ENROLLMENT_COLUMNS, 1):
        if field_name == "parent_phone":
            phone_col = idx
            break
    assert phone_col is not None

    # Row 3 is the first data row. Must be text format.
    phone_cell = ws.cell(row=3, column=phone_col)
    assert phone_cell.number_format == "@", (
        f"parent_phone cell row 3 is {phone_cell.number_format!r}, expected '@'"
    )

    # Spot-check one more data row.
    phone_cell = ws.cell(row=50, column=phone_col)
    assert phone_cell.number_format == "@"


def test_header_rows_locked_data_rows_unlocked():
    """Rows 1-2 must be locked under sheet protection; row 3+ data
    cells must be unlocked so admins can still type."""
    svc = EnrollmentTemplateService()
    xlsx_bytes = svc.generate_template(class_names=["Grade 1"])
    wb = load_workbook(BytesIO(xlsx_bytes))
    ws = wb.active

    assert ws.protection.sheet is True, "sheet protection should be enabled"

    # Header rows locked
    assert ws.cell(row=1, column=1).protection.locked is True
    assert ws.cell(row=2, column=1).protection.locked is True

    # Data rows unlocked across a few columns
    assert ws.cell(row=3, column=1).protection.locked is False
    assert ws.cell(row=3, column=5).protection.locked is False
    assert ws.cell(row=10, column=1).protection.locked is False


def test_parse_excel_rescues_phone_from_float():
    """A phone number that an old (non-text-formatted) template stored
    as a float must parse back to a digit string — not scientific
    notation. Guards against legacy templates still in circulation."""
    # Build a minimal workbook that mimics an old template: no text
    # format on phone column. Excel/openpyxl will store "263771234567"
    # as a float in the cell.
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    # Row 1: field names
    field_names = [col[0] for col in ENROLLMENT_COLUMNS]
    for col_idx, name in enumerate(field_names, 1):
        ws.cell(row=1, column=col_idx, value=name)
    # Row 2: labels (ignored by parser)
    for col_idx, (_, label, _, _) in enumerate(ENROLLMENT_COLUMNS, 1):
        ws.cell(row=2, column=col_idx, value=label)
    # Row 3: a student with a phone stored as a float
    phone_col_idx = field_names.index("parent_phone") + 1
    ws.cell(row=3, column=1, value="Jane")
    ws.cell(row=3, column=2, value="Doe")
    # Big phone number as a float — how Excel stores it without text format
    ws.cell(row=3, column=phone_col_idx, value=263771234567.0)

    out = BytesIO()
    wb.save(out)
    out.seek(0)

    rows, count = EnrollmentTemplateService.parse_excel(out.getvalue())
    assert count == 1
    assert rows[0]["parent_phone"] == "263771234567", (
        f"phone should be digit string, got {rows[0]['parent_phone']!r}"
    )


def test_text_format_fields_matches_actual_columns():
    """Guard rail: every field in TEXT_FORMAT_FIELDS must exist as a
    real column, so a typo doesn't silently skip the format pass."""
    field_names = {col[0] for col in ENROLLMENT_COLUMNS}
    for field in TEXT_FORMAT_FIELDS:
        assert field in field_names, (
            f"TEXT_FORMAT_FIELDS references {field!r} which is not in "
            f"ENROLLMENT_COLUMNS"
        )
