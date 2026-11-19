"""MIS XLSX export helpers — Phase 6.

Each function takes an already-computed data dict (from the mis_service
public methods) and renders a styled XLSX workbook as bytes. The web
endpoints call the service's analytics method first, then pass its
output here — keeping the analytics code free of presentation concerns
and letting the same number crunching power the browser chart, the
XLSX export and the future PDF.

Why XLSX and not CSV: the MIS views are hierarchical (rules + counts
+ per-student trip breakdown, or per-term + per-subject breakdown)
and don't flatten into a single table without losing shape. Excel
workbooks with multiple sheets keep that shape intact.

PDF export: each MIS page already prints cleanly via the browser's
Ctrl+P → Save as PDF path (print stylesheets suppress nav + action
buttons). Custom WeasyPrint templates can come later if a bespoke
layout is wanted.
"""

from __future__ import annotations

from datetime import date
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HEADER_FILL = PatternFill(start_color="1B3A6B", end_color="1B3A6B", fill_type="solid")
HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
SECTION_FILL = PatternFill(start_color="F3F4F6", end_color="F3F4F6", fill_type="solid")
SECTION_FONT = Font(bold=True, color="1B3A6B", size=12)


def _style_header_row(ws, row_num: int, col_count: int) -> None:
    for c in range(1, col_count + 1):
        cell = ws.cell(row=row_num, column=c)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")


def _section_title(ws, row_num: int, title: str, span_cols: int = 4) -> None:
    ws.cell(row=row_num, column=1, value=title).font = SECTION_FONT
    ws.cell(row=row_num, column=1).fill = SECTION_FILL
    for c in range(2, span_cols + 1):
        ws.cell(row=row_num, column=c).fill = SECTION_FILL


def _widen(ws, widths: list[int]) -> None:
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _finish(wb: Workbook) -> bytes:
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


# --------------------------------------------------------------------- #
# Executive Dashboard — kpi_summary
# --------------------------------------------------------------------- #


def kpi_summary_xlsx(data: dict, tenant_name: str) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "KPI summary"

    window = data.get("window", {}) or {}
    row = 1
    ws.cell(row=row, column=1, value=f"Executive Dashboard — {tenant_name}").font = Font(bold=True, size=14)
    row += 1
    ws.cell(row=row, column=1, value=f"{window.get('from_date')} to {window.get('to_date')}")
    row += 2

    # Academic KPIs
    _section_title(ws, row, "Academic")
    row += 1
    academic = data.get("academic", {}) or {}
    rows = [
        ("Overall average", f"{academic.get('overall_average', 0):.1f}%"),
        ("Pass rate", f"{academic.get('pass_rate_pct', 0):.1f}%"),
        ("Students with marks", academic.get("students_with_marks", 0)),
        ("Top class", (academic.get("top_class") or {}).get("class_name") or "—"),
        ("Top class average", f"{(academic.get('top_class') or {}).get('mean_percentage', 0):.1f}%"
            if academic.get("top_class") else "—"),
        ("Bottom class", (academic.get("bottom_class") or {}).get("class_name") or "—"),
        ("Top subject", (academic.get("top_subject") or {}).get("subject_name") or "—"),
        ("Bottom subject", (academic.get("bottom_subject") or {}).get("subject_name") or "—"),
    ]
    for label, val in rows:
        ws.cell(row=row, column=1, value=label).font = Font(bold=True)
        ws.cell(row=row, column=2, value=val)
        row += 1

    row += 1
    _section_title(ws, row, "Attendance")
    row += 1
    att = data.get("attendance", {}) or {}
    att_rows = [
        ("Rate", f"{att.get('rate_pct', 0):.1f}%"),
        ("Present", att.get("present", 0)),
        ("Late", att.get("late", 0)),
        ("Excused", att.get("excused", 0)),
        ("Absent", att.get("absent", 0)),
        ("Total sessions", att.get("total", 0)),
    ]
    for label, val in att_rows:
        ws.cell(row=row, column=1, value=label).font = Font(bold=True)
        ws.cell(row=row, column=2, value=val)
        row += 1

    row += 1
    _section_title(ws, row, "At risk")
    row += 1
    risk = data.get("at_risk", {}) or {}
    ws.cell(row=row, column=1, value="Total at risk").font = Font(bold=True)
    ws.cell(row=row, column=2, value=risk.get("count", 0))
    row += 1
    ws.cell(row=row, column=1, value="Chronic absentees").font = Font(bold=True)
    ws.cell(row=row, column=2, value=risk.get("chronic_absentees_count", 0))
    row += 2

    # Class rollup
    _section_title(ws, row, "Class rollup", span_cols=4)
    row += 1
    _style_header_row(ws, row, 3)
    for col, label in enumerate(["Class", "Students", "Mean %"], 1):
        ws.cell(row=row, column=col, value=label)
    row += 1
    for c in data.get("class_rollup", []) or []:
        ws.cell(row=row, column=1, value=c.get("class_name"))
        ws.cell(row=row, column=2, value=c.get("student_count"))
        ws.cell(row=row, column=3, value=c.get("mean_percentage"))
        row += 1

    row += 1
    _section_title(ws, row, "Subject rollup", span_cols=4)
    row += 1
    _style_header_row(ws, row, 3)
    for col, label in enumerate(["Subject", "Students", "Mean %"], 1):
        ws.cell(row=row, column=col, value=label)
    row += 1
    for s in data.get("subject_rollup", []) or []:
        ws.cell(row=row, column=1, value=s.get("subject_name"))
        ws.cell(row=row, column=2, value=s.get("student_count"))
        ws.cell(row=row, column=3, value=s.get("mean_percentage"))
        row += 1

    _widen(ws, [34, 22, 22])
    return _finish(wb)


# --------------------------------------------------------------------- #
# Subject performance (Phase 2)
# --------------------------------------------------------------------- #


def subject_performance_xlsx(data: dict, tenant_name: str) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Subject performance"

    window = data.get("window", {}) or {}
    ws.cell(row=1, column=1, value=f"Subject performance — {tenant_name}").font = Font(bold=True, size=14)
    ws.cell(row=2, column=1, value=f"{window.get('from_date')} to {window.get('to_date')}")
    ws.cell(row=3, column=1, value=f"Pass mark: {data.get('pass_mark_pct')}%")

    headers = [
        "Subject", "Students", "Mean %", "Median %",
        "Distinction %", "Merit %", "Credit %",
        "Pass %", "Fail %",
        "Best student", "Best %", "Weakest student", "Weakest %",
    ]
    _style_header_row(ws, 5, len(headers))
    for col, label in enumerate(headers, 1):
        ws.cell(row=5, column=col, value=label)

    row = 6
    for s in data.get("subjects", []) or []:
        r = s.get("rates", {}) or {}
        best = s.get("best_student") or {}
        worst = s.get("worst_student") or {}
        ws.cell(row=row, column=1, value=s.get("subject_name"))
        ws.cell(row=row, column=2, value=s.get("student_count"))
        ws.cell(row=row, column=3, value=s.get("mean"))
        ws.cell(row=row, column=4, value=s.get("median"))
        ws.cell(row=row, column=5, value=r.get("distinction_pct"))
        ws.cell(row=row, column=6, value=r.get("merit_pct"))
        ws.cell(row=row, column=7, value=r.get("credit_pct"))
        ws.cell(row=row, column=8, value=r.get("pass_pct"))
        ws.cell(row=row, column=9, value=r.get("fail_pct"))
        ws.cell(row=row, column=10, value=best.get("name"))
        ws.cell(row=row, column=11, value=best.get("percentage"))
        ws.cell(row=row, column=12, value=worst.get("name"))
        ws.cell(row=row, column=13, value=worst.get("percentage"))
        row += 1

    _widen(ws, [26, 10, 10, 10, 14, 10, 10, 10, 10, 24, 10, 24, 10])
    return _finish(wb)


# --------------------------------------------------------------------- #
# Grade distribution (Phase 3)
# --------------------------------------------------------------------- #


def grade_distribution_xlsx(data: dict, tenant_name: str) -> bytes:
    wb = Workbook()

    # Summary sheet
    ws = wb.active
    ws.title = "Summary"
    window = data.get("window", {}) or {}
    ws.cell(row=1, column=1, value=f"Grade distribution — {tenant_name}").font = Font(bold=True, size=14)
    ws.cell(row=2, column=1, value=f"{window.get('from_date')} to {window.get('to_date')}")
    rows = [
        ("Entries", data.get("n", 0)),
        ("Mean", data.get("mean", 0)),
        ("Standard deviation", data.get("stddev", 0)),
        ("Pass mark", f"{data.get('pass_mark_pct', 0)}%"),
        ("Bin size", data.get("bin_size", 10)),
    ]
    row = 4
    for label, val in rows:
        ws.cell(row=row, column=1, value=label).font = Font(bold=True)
        ws.cell(row=row, column=2, value=val)
        row += 1
    _widen(ws, [26, 14])

    # Percentage histogram sheet
    ws2 = wb.create_sheet("Percentage bands")
    _style_header_row(ws2, 1, 3)
    for col, label in enumerate(["Band", "Count", "% of entries"], 1):
        ws2.cell(row=1, column=col, value=label)
    r = 2
    for b in data.get("histogram", []) or []:
        ws2.cell(row=r, column=1, value=b.get("label"))
        ws2.cell(row=r, column=2, value=b.get("count"))
        ws2.cell(row=r, column=3, value=b.get("percentage"))
        r += 1
    _widen(ws2, [18, 10, 14])

    # Letter grade sheet
    ws3 = wb.create_sheet("Letter grades")
    _style_header_row(ws3, 1, 3)
    for col, label in enumerate(["Grade", "Count", "% of entries"], 1):
        ws3.cell(row=1, column=col, value=label)
    r = 2
    for b in data.get("band_histogram", []) or []:
        ws3.cell(row=r, column=1, value=b.get("grade"))
        ws3.cell(row=r, column=2, value=b.get("count"))
        ws3.cell(row=r, column=3, value=b.get("percentage"))
        r += 1
    _widen(ws3, [10, 10, 14])

    return _finish(wb)


# --------------------------------------------------------------------- #
# Merit list (Phase 3)
# --------------------------------------------------------------------- #


def merit_list_xlsx(data: dict, tenant_name: str) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Ranking"
    window = data.get("window", {}) or {}
    ws.cell(row=1, column=1, value=f"Merit list — {tenant_name}").font = Font(bold=True, size=14)
    ws.cell(row=2, column=1,
            value=f"Scope: {data.get('scope')} · {window.get('from_date')} to {window.get('to_date')} · {data.get('total_in_scope')} ranked")

    _style_header_row(ws, 4, 4)
    for col, label in enumerate(["Rank", "Student", "Class", "Aggregate %"], 1):
        ws.cell(row=4, column=col, value=label)
    r = 5
    for row_data in data.get("ranking", []) or []:
        ws.cell(row=r, column=1, value=row_data.get("rank"))
        ws.cell(row=r, column=2, value=row_data.get("student_name"))
        ws.cell(row=r, column=3, value=row_data.get("class_name"))
        ws.cell(row=r, column=4, value=row_data.get("mean_percentage"))
        r += 1
    _widen(ws, [8, 32, 24, 14])
    return _finish(wb)


# --------------------------------------------------------------------- #
# Longitudinal (Phase 4)
# --------------------------------------------------------------------- #


def student_longitudinal_xlsx(data: dict, tenant_name: str) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Timeline"
    student_name = data.get("student_name") or "Student"
    window = data.get("window", {}) or {}
    ws.cell(row=1, column=1, value=f"{student_name} — Longitudinal performance").font = Font(bold=True, size=14)
    ws.cell(row=2, column=1, value=f"{tenant_name}  ·  {window.get('from_date')} to {window.get('to_date')}")

    # Term-by-term timeline
    headers = ["Term", "Aggregate %", "Grade", "Rank in class", "Class size",
               "Δ Aggregate %", "Δ Rank"]
    _style_header_row(ws, 4, len(headers))
    for col, label in enumerate(headers, 1):
        ws.cell(row=4, column=col, value=label)
    r = 5
    for t in data.get("terms", []) or []:
        ws.cell(row=r, column=1, value=t.get("label"))
        ws.cell(row=r, column=2, value=t.get("aggregate_pct"))
        ws.cell(row=r, column=3, value=t.get("grade"))
        ws.cell(row=r, column=4, value=t.get("rank_in_class"))
        ws.cell(row=r, column=5, value=t.get("class_size"))
        ws.cell(row=r, column=6, value=t.get("aggregate_delta_pct"))
        ws.cell(row=r, column=7, value=t.get("rank_delta"))
        r += 1
    _widen(ws, [18, 14, 10, 14, 12, 16, 10])

    # Strengths / weaknesses sheet
    ws2 = wb.create_sheet("Strengths & weaknesses")
    ws2.cell(row=1, column=1, value="Strengths (top 3)").font = Font(bold=True)
    r = 2
    for s in data.get("strengths", []) or []:
        ws2.cell(row=r, column=1, value=s.get("subject_name"))
        ws2.cell(row=r, column=2, value=s.get("mean_percentage"))
        r += 1
    r += 1
    ws2.cell(row=r, column=1, value="Weaknesses (bottom 3)").font = Font(bold=True)
    r += 1
    for s in data.get("weaknesses", []) or []:
        ws2.cell(row=r, column=1, value=s.get("subject_name"))
        ws2.cell(row=r, column=2, value=s.get("mean_percentage"))
        r += 1
    _widen(ws2, [26, 14])

    return _finish(wb)


# --------------------------------------------------------------------- #
# Early warning (Phase 5)
# --------------------------------------------------------------------- #


def early_warning_xlsx(data: dict, tenant_name: str) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Flagged students"
    window = data.get("window", {}) or {}
    ws.cell(row=1, column=1, value=f"Early warning — {tenant_name}").font = Font(bold=True, size=14)
    ws.cell(row=2, column=1, value=f"{window.get('from_date')} to {window.get('to_date')}")
    counts = data.get("counts", {}) or {}
    ws.cell(row=3, column=1, value=(
        f"Critical: {counts.get('CRITICAL', 0)}  ·  High: {counts.get('HIGH', 0)}  ·  "
        f"Medium: {counts.get('MEDIUM', 0)}  ·  Low: {counts.get('LOW', 0)}"
    ))

    headers = ["Level", "Student", "Class", "Aggregate %", "Attendance %",
               "Decline %", "Triggered rules"]
    _style_header_row(ws, 5, len(headers))
    for col, label in enumerate(headers, 1):
        ws.cell(row=5, column=col, value=label)
    r = 6
    for s in data.get("students", []) or []:
        trips = "; ".join(
            f"{t.get('kind')}: {t.get('level')} ({t.get('message')})"
            for t in (s.get("triggered_rules") or [])
        )
        ws.cell(row=r, column=1, value=s.get("level"))
        ws.cell(row=r, column=2, value=s.get("student_name"))
        ws.cell(row=r, column=3, value=s.get("class_name"))
        ws.cell(row=r, column=4, value=s.get("aggregate_pct"))
        ws.cell(row=r, column=5, value=s.get("attendance_pct"))
        ws.cell(row=r, column=6, value=s.get("decline_pct"))
        ws.cell(row=r, column=7, value=trips)
        r += 1
    _widen(ws, [10, 28, 20, 14, 14, 12, 60])

    # Rules sheet so the admin reading the file knows what thresholds
    # were in force when the scan ran
    ws2 = wb.create_sheet("Rules")
    _style_header_row(ws2, 1, 2)
    for col, label in enumerate(["Rule", "Value"], 1):
        ws2.cell(row=1, column=col, value=label)
    r = 2
    for k, v in (data.get("rules") or {}).items():
        ws2.cell(row=r, column=1, value=k)
        ws2.cell(row=r, column=2, value=v)
        r += 1
    _widen(ws2, [32, 12])

    return _finish(wb)
