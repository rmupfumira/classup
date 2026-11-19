"""Phase 6 — MIS XLSX export tests.

The export helpers take dict output from analytics methods and produce
XLSX bytes. These tests verify the bytes are actually a valid workbook,
the expected sheets exist, and the data from the input dict is present
in the output cells. Rather than smoking the HTTP endpoint (needs auth
+ running app), we test the pure render functions directly.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from openpyxl import load_workbook

from app.services import mis_export


SAMPLE_TENANT = "Test Academy"


# --------------------------------------------------------------------- #
# kpi_summary
# --------------------------------------------------------------------- #


def test_kpi_summary_xlsx_round_trips():
    data = {
        "window": {"from_date": "2026-01-01", "to_date": "2026-03-31",
                   "class_id": None},
        "academic": {
            "overall_average": 72.5,
            "pass_rate_pct": 88.0,
            "students_with_marks": 42,
            "top_class": {"class_id": "x", "class_name": "6A",
                          "mean_percentage": 81.2, "student_count": 20},
            "bottom_class": {"class_id": "y", "class_name": "6C",
                             "mean_percentage": 62.0, "student_count": 20},
            "top_subject": {"subject_id": "x", "subject_name": "English",
                            "mean_percentage": 80.0, "student_count": 42},
            "bottom_subject": None,
        },
        "attendance": {
            "total": 100, "present": 90, "late": 5, "excused": 2, "absent": 3,
            "rate_pct": 97.0,
        },
        "at_risk": {"count": 4, "chronic_absentees_count": 2,
                    "chronic_absentees": []},
        "class_rollup": [
            {"class_id": "x", "class_name": "6A", "student_count": 20, "mean_percentage": 81.2},
            {"class_id": "y", "class_name": "6B", "student_count": 20, "mean_percentage": 70.0},
        ],
        "subject_rollup": [
            {"subject_id": "s1", "subject_name": "English", "student_count": 42, "mean_percentage": 80.0},
        ],
    }
    xlsx = mis_export.kpi_summary_xlsx(data, SAMPLE_TENANT)
    assert xlsx[:4] == b"PK\x03\x04"  # valid zip
    wb = load_workbook(BytesIO(xlsx))
    assert "KPI summary" in wb.sheetnames
    ws = wb["KPI summary"]
    # Title row
    assert SAMPLE_TENANT in (ws["A1"].value or "")
    # The academic KPIs land somewhere in column B
    values_by_label = {}
    for row in ws.iter_rows(min_row=1, max_row=40, values_only=True):
        if row and row[0]:
            values_by_label[row[0]] = row[1] if len(row) > 1 else None
    assert "Overall average" in values_by_label
    assert "72.5%" in str(values_by_label["Overall average"])
    assert "Top class" in values_by_label
    assert values_by_label["Top class"] == "6A"


# --------------------------------------------------------------------- #
# subject_performance
# --------------------------------------------------------------------- #


def test_subject_performance_xlsx_rows_render():
    data = {
        "window": {"from_date": "2026-01-01", "to_date": "2026-03-31", "class_id": None},
        "grading_system": {"bands": []},
        "pass_mark_pct": 50.0,
        "subjects": [
            {
                "subject_id": "s1", "subject_name": "English",
                "student_count": 10,
                "mean": 72.0, "median": 70.0,
                "rates": {
                    "distinction_pct": 20.0, "merit_pct": 30.0,
                    "credit_pct": 30.0, "pass_pct": 90.0, "fail_pct": 10.0,
                },
                "band_counts": {"A": 2},
                "best_student": {"id": "a", "name": "Alice", "percentage": 95.0},
                "worst_student": {"id": "b", "name": "Bob", "percentage": 40.0},
            },
        ],
    }
    xlsx = mis_export.subject_performance_xlsx(data, SAMPLE_TENANT)
    wb = load_workbook(BytesIO(xlsx))
    ws = wb.active
    # Headers on row 5 (after title + dates + pass mark + blank)
    headers = [c.value for c in ws[5]]
    assert "Subject" in headers
    assert "Pass %" in headers
    # Row 6 has English
    first_row = [c.value for c in ws[6]]
    assert "English" in first_row
    # Pass rate landed in the right column
    pass_col_idx = headers.index("Pass %")
    assert first_row[pass_col_idx] == 90.0


# --------------------------------------------------------------------- #
# grade_distribution — multi-sheet
# --------------------------------------------------------------------- #


def test_grade_distribution_xlsx_has_three_sheets():
    data = {
        "window": {"from_date": "2026-01-01", "to_date": "2026-03-31",
                   "class_id": None, "subject_id": None},
        "n": 10, "mean": 65.0, "stddev": 12.0, "pass_mark_pct": 50.0,
        "bin_size": 10,
        "histogram": [
            {"label": "60-69%", "lo": 60, "hi": 69, "count": 5, "percentage": 50.0},
            {"label": "70-79%", "lo": 70, "hi": 79, "count": 5, "percentage": 50.0},
        ],
        "band_histogram": [
            {"grade": "C", "count": 5, "percentage": 50.0},
            {"grade": "B", "count": 5, "percentage": 50.0},
        ],
    }
    xlsx = mis_export.grade_distribution_xlsx(data, SAMPLE_TENANT)
    wb = load_workbook(BytesIO(xlsx))
    assert set(wb.sheetnames) >= {"Summary", "Percentage bands", "Letter grades"}
    # The letter grades sheet has B + C
    grades = [row[0].value for row in wb["Letter grades"].iter_rows(min_row=2) if row[0].value]
    assert "B" in grades
    assert "C" in grades


# --------------------------------------------------------------------- #
# merit_list
# --------------------------------------------------------------------- #


def test_merit_list_xlsx_rows():
    data = {
        "window": {"from_date": "2026-01-01", "to_date": "2026-03-31",
                   "class_id": None, "grade_level_id": None},
        "scope": "SCHOOL",
        "total_in_scope": 3,
        "ranking": [
            {"rank": 1, "student_id": "a", "student_name": "Alice",
             "class_id": "x", "class_name": "6A", "mean_percentage": 95.0},
            {"rank": 2, "student_id": "b", "student_name": "Bob",
             "class_id": "x", "class_name": "6A", "mean_percentage": 80.0},
            {"rank": 3, "student_id": "c", "student_name": "Carol",
             "class_id": "y", "class_name": "6B", "mean_percentage": 70.0},
        ],
    }
    xlsx = mis_export.merit_list_xlsx(data, SAMPLE_TENANT)
    wb = load_workbook(BytesIO(xlsx))
    ws = wb.active
    assert ws.cell(row=4, column=1).value == "Rank"
    assert ws.cell(row=5, column=2).value == "Alice"
    assert ws.cell(row=5, column=4).value == 95.0
    assert ws.cell(row=7, column=2).value == "Carol"


# --------------------------------------------------------------------- #
# longitudinal — 2 sheets
# --------------------------------------------------------------------- #


def test_longitudinal_xlsx_contains_timeline_and_strengths():
    data = {
        "student_id": "x",
        "student_name": "Alice Ace",
        "class_id": "c",
        "window": {"from_date": "2026-01-01", "to_date": "2026-12-31"},
        "terms": [
            {"year": 2026, "term": 1, "label": "Term 1 2026",
             "aggregate_pct": 60.0, "grade": "C", "bucket": "credit",
             "rank_in_class": 3, "class_size": 20,
             "aggregate_delta_pct": None, "rank_delta": None,
             "subjects": []},
            {"year": 2026, "term": 2, "label": "Term 2 2026",
             "aggregate_pct": 75.0, "grade": "B", "bucket": "merit",
             "rank_in_class": 1, "class_size": 20,
             "aggregate_delta_pct": 15.0, "rank_delta": 2,
             "subjects": []},
        ],
        "strengths": [
            {"subject_id": "s1", "subject_name": "English", "mean_percentage": 80.0},
        ],
        "weaknesses": [
            {"subject_id": "s2", "subject_name": "Math", "mean_percentage": 55.0},
        ],
    }
    xlsx = mis_export.student_longitudinal_xlsx(data, SAMPLE_TENANT)
    wb = load_workbook(BytesIO(xlsx))
    assert "Timeline" in wb.sheetnames
    assert "Strengths & weaknesses" in wb.sheetnames
    ws = wb["Timeline"]
    # Term 2 landed
    row6 = [c.value for c in ws[6]]
    assert "Term 2 2026" in row6


# --------------------------------------------------------------------- #
# early_warning — flagged students + rules
# --------------------------------------------------------------------- #


def test_early_warning_xlsx_contains_both_sheets():
    data = {
        "window": {"from_date": "2026-01-01", "to_date": "2026-03-31", "class_id": None},
        "rules": {"aggregate_avg_critical": 40.0, "attendance_critical": 60.0},
        "counts": {"CRITICAL": 1, "HIGH": 0, "MEDIUM": 0, "LOW": 0},
        "total_at_risk": 1,
        "students": [
            {
                "student_id": "s", "student_name": "Alice Low",
                "class_id": "c", "class_name": "6A",
                "level": "CRITICAL",
                "aggregate_pct": 25.0,
                "attendance_pct": None,
                "decline_pct": None,
                "triggered_rules": [
                    {"kind": "aggregate", "level": "CRITICAL",
                     "message": "Aggregate 25.0%", "value": 25.0},
                ],
            },
        ],
    }
    xlsx = mis_export.early_warning_xlsx(data, SAMPLE_TENANT)
    wb = load_workbook(BytesIO(xlsx))
    assert "Flagged students" in wb.sheetnames
    assert "Rules" in wb.sheetnames
    # Student row lands with CRITICAL + name
    ws = wb["Flagged students"]
    row6 = [c.value for c in ws[6]]
    assert "CRITICAL" in row6
    assert "Alice Low" in row6
    # Rules sheet has at least one rule
    rules_vals = [
        row[0].value for row in wb["Rules"].iter_rows(min_row=2)
        if row[0].value
    ]
    assert "aggregate_avg_critical" in rules_vals
