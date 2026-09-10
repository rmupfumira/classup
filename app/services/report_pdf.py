"""Generate a PDF for a DailyReport.

Primary path: WeasyPrint. Renders the same Jinja2 template that mirrors
the web view (``app/templates/reports/pdf.html``), then rasterises to
PDF via libpango. The prod Dockerfile installs the required system
libs (libpango, libpangoft2, libharfbuzz, fonts-liberation).

Fallback: fpdf2 pure-Python renderer, kept because WeasyPrint's system
deps are painful to install on Windows developer machines. If the
WeasyPrint import fails we log once and use fpdf2 — output is uglier
but the invariants tests rely on (returns %PDF bytes, doesn't crash on
weird data) still hold.

Section renderers cover every ReportTemplateSection type in the schema
(per CLAUDE.md):
  CHECKLIST · REPEATABLE_ENTRIES · NARRATIVE · ACADEMIC_GRADES ·
  MEALS · INFO_DISPLAY · SUMMARY · SIGNATURES
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fpdf import FPDF
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailyReport, Tenant

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "reports"
_PDF_TEMPLATE_NAME = "pdf.html"

# Cached — probing WeasyPrint on every render is wasteful, and on Windows
# the failure is loud enough (a chain of DLL misses) that we don't want
# to hit it more than once per process.
_weasyprint_available: bool | None = None


def _try_import_weasyprint():
    """Import weasyprint once. Returns the module or None.

    We check by import rather than by a flag so the fallback triggers
    for ANY reason WeasyPrint won't load — missing lib, missing font
    config, wrong glibc — not just the happy "not installed" case.
    """
    global _weasyprint_available
    if _weasyprint_available is False:
        return None
    try:
        import weasyprint  # noqa: F401  (imported for side effects too)
        _weasyprint_available = True
        return weasyprint
    except Exception as exc:
        if _weasyprint_available is None:
            # Log once, at info — Windows dev boxes hit this path
            # normally and we don't want alert fatigue.
            logger.info(
                "WeasyPrint unavailable, falling back to fpdf2 renderer: %s",
                exc,
            )
        _weasyprint_available = False
        return None


_jinja_env: Environment | None = None


def _get_jinja_env() -> Environment:
    """Standalone Jinja env for the PDF template. Not the main app's
    Environment — we don't want the base template's globals here (i18n,
    settings, etc.) since none of them apply to a standalone PDF."""
    global _jinja_env
    if _jinja_env is not None:
        return _jinja_env
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    env.globals["section_color_class"] = _section_color_class
    env.globals["grade_for"] = _grade_for_template
    _jinja_env = env
    return env


async def render_report_pdf(
    db: AsyncSession, report: DailyReport,
) -> bytes:
    """Render one report to PDF bytes.

    Loads the tenant name for the header. All other data comes off the
    report's eager-loaded relationships (student, school_class, template)
    plus its ``report_data`` JSONB.
    """
    tenant = None
    if report.tenant_id:
        tenant = await db.get(Tenant, report.tenant_id)
    tenant_name = tenant.name if tenant else "ClassUp"

    weasy = _try_import_weasyprint()
    if weasy is not None:
        try:
            return _render_with_weasyprint(weasy, report, tenant_name)
        except Exception:
            # A template bug or runtime error shouldn't lose the PDF —
            # the fpdf2 renderer is functionally equivalent, just uglier.
            logger.exception(
                "WeasyPrint render failed for report=%s, falling back to fpdf2",
                report.id,
            )
    return _render_with_fpdf(report, tenant_name)


# ---------------------------------------------------------------------------
# WeasyPrint path — the pretty one, matches the web view.
# ---------------------------------------------------------------------------


def _render_with_weasyprint(weasyprint_module, report: DailyReport, tenant_name: str) -> bytes:
    student_first = "Student"
    if report.student:
        student_first = (report.student.first_name or "Student").strip() or "Student"

    class_name = report.school_class.name if report.school_class else ""

    template_sections = (
        report.template.sections if report.template and report.template.sections else []
    )
    # display_order sort so section rendering matches the web view.
    sorted_sections = sorted(template_sections, key=lambda s: s.get("display_order", 0))

    report_sections: dict[str, Any] = (
        (report.report_data or {}).get("sections", {}) if report.report_data else {}
    )

    # Teacher notes are a separate section in the JSONB (mirrors view.html).
    teacher_notes = report_sections.get("notes", {}) if isinstance(report_sections, dict) else {}
    teacher_notes_body = teacher_notes.get("content", "") if isinstance(teacher_notes, dict) else ""

    created_by_name = ""
    created_by = getattr(report, "created_by_user", None)
    if created_by is not None:
        first = getattr(created_by, "first_name", "") or ""
        last = getattr(created_by, "last_name", "") or ""
        created_by_name = f"{first} {last}".strip()

    context = {
        "student_first_name": student_first,
        "tenant_name": tenant_name,
        "class_name": class_name,
        "report_date_pretty": report.report_date.strftime("%A, %d %B %Y") if report.report_date else "",
        "status": (getattr(report, "status", "") or "").upper() or ("FINALIZED" if report.finalized_at else "DRAFT"),
        "sections": sorted_sections,
        "report_sections": report_sections,
        "teacher_notes_body": teacher_notes_body,
        "finalized_at": report.finalized_at.strftime("%d %b %Y, %H:%M") if report.finalized_at else "",
        "created_by_name": created_by_name,
    }

    template = _get_jinja_env().get_template(_PDF_TEMPLATE_NAME)
    html_str = template.render(**context)

    # base_url lets WeasyPrint resolve any relative asset paths. We
    # currently keep everything inline (no <img>, no @import) so this is
    # belt-and-braces — but if someone drops a school logo <img
    # src="/static/..."> into the template later, it'll Just Work.
    static_root = Path(__file__).resolve().parent.parent
    pdf_bytes = weasyprint_module.HTML(
        string=html_str, base_url=str(static_root),
    ).write_pdf()
    return pdf_bytes


def _section_color_class(color: str | None) -> str:
    """Map a section.color value (Tailwind name or hex) to one of the
    five CSS classes defined in pdf.html. Kept in sync with view.html's
    color mapping — that's the source of truth for what admins see when
    they configure a report template."""
    if not color:
        return "color-violet"
    c = str(color).lower()
    if c in {"green", "#10b981", "#059669", "#34d399"}:
        return "color-green"
    if c in {"orange", "#f59e0b", "#d97706", "#fbbf24"}:
        return "color-orange"
    if c in {"blue", "#3b82f6", "#2563eb", "#1b3a6b"}:
        return "color-blue"
    if c in {"red", "#ef4444", "#dc2626"}:
        return "color-red"
    # violet is the default — covers "purple", "violet", the various
    # violet hexes, and anything unrecognised.
    return "color-violet"


def _grade_for_template(marks, total, grading) -> str:
    """Jinja-callable wrapper around _grade_for. Accepts loose types
    because JSONB round-trips numbers as ints or floats depending on
    source."""
    try:
        marks_num = float(marks) if marks is not None else None
    except (TypeError, ValueError):
        marks_num = None
    return _grade_for(marks_num, total or 100, grading or [])


# ---------------------------------------------------------------------------
# fpdf2 fallback path — kept for Windows dev without libpango.
#
# Exact code path we shipped before switching to WeasyPrint; every
# tests/test_services/test_report_pdf.py invariant relies on this
# still working end-to-end.
# ---------------------------------------------------------------------------


def _render_with_fpdf(report: DailyReport, tenant_name: str) -> bytes:
    student_name = "Student"
    if report.student:
        student_name = f"{report.student.first_name} {report.student.last_name}".strip()

    class_name = report.school_class.name if report.school_class else "(no class)"
    template_name = report.template.name if report.template else "Report"
    report_type = report.template.report_type if report.template else ""

    pdf = _ReportPDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 10, tenant_name, new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(30, 30, 30)
    pdf.cell(0, 8, template_name, new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(35, 6, "Student:", new_x="RIGHT")
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 6, student_name, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(35, 6, "Class:", new_x="RIGHT")
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 6, class_name, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(35, 6, "Report date:", new_x="RIGHT")
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 6, report.report_date.strftime("%A %d %B %Y"), new_x="LMARGIN", new_y="NEXT")
    if report_type:
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(35, 6, "Type:", new_x="RIGHT")
        pdf.set_font("Helvetica", "", 11)
        pdf.cell(0, 6, report_type.replace("_", " ").title(), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    _hr(pdf)
    pdf.ln(4)

    template_sections = (
        report.template.sections if report.template and report.template.sections else []
    )
    report_sections: dict[str, Any] = (
        (report.report_data or {}).get("sections", {}) if report.report_data else {}
    )

    for section in sorted(template_sections, key=lambda s: s.get("display_order", 0)):
        section_id = section.get("id")
        section_type = (section.get("type") or "").upper()
        section_data = report_sections.get(section_id, {}) if section_id else {}
        _render_section_header(pdf, section)

        try:
            renderer = _SECTION_RENDERERS.get(section_type, _render_generic)
            renderer(pdf, section, section_data)
        except Exception:
            logger.exception(
                "Report section render failed: report=%s section=%s type=%s",
                report.id, section_id, section_type,
            )
            pdf.set_font("Helvetica", "I", 9)
            pdf.set_text_color(180, 80, 80)
            pdf.multi_cell(0, 5, "(This section could not be rendered.)")
            pdf.set_text_color(0, 0, 0)

        pdf.ln(4)

    pdf.ln(4)
    _hr(pdf)
    pdf.ln(2)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(120, 120, 120)
    finalized = report.finalized_at.strftime("%d %b %Y %H:%M") if report.finalized_at else "not finalised"
    pdf.cell(0, 4, f"Finalised: {finalized}. Generated by ClassUp.", new_x="LMARGIN", new_y="NEXT")

    output = pdf.output()
    return bytes(output) if isinstance(output, bytearray) else output


class _ReportPDF(FPDF):
    pass


def _hr(pdf: FPDF) -> None:
    pdf.set_draw_color(210, 210, 210)
    x = pdf.get_x()
    y = pdf.get_y()
    w = pdf.w - pdf.l_margin - pdf.r_margin
    pdf.line(x, y, x + w, y)


def _render_section_header(pdf: FPDF, section: dict[str, Any]) -> None:
    title = section.get("title") or "(untitled section)"
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_fill_color(240, 240, 245)
    pdf.set_text_color(30, 30, 30)
    pdf.cell(0, 7, f"  {title}", fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)


def _render_kv_row(pdf: FPDF, label: str, value: str) -> None:
    """One label + value row.

    Uses pdf.write() rather than cell + multi_cell. write() flows text
    naturally across the page width without any cursor-drift trap:
    fpdf2's multi_cell(w=0, ...) computes remaining width from the
    current x, and a previous multi_cell's ``new_y="NEXT"`` doesn't
    always reset x to the left margin — leaving the next multi_cell
    with zero remaining space and the exception:
      Not enough horizontal space to render a single character
    """
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "B", 10)
    pdf.write(5.5, f"{label}: ")
    pdf.set_font("Helvetica", "", 10)
    val = str(value if value not in (None, "") else "-")[:2000]
    pdf.write(5.5, val)
    pdf.ln(5.5)


def _render_checklist(pdf: FPDF, section: dict, data: dict) -> None:
    for field in section.get("fields") or []:
        label = field.get("label") or field.get("id", "field")
        raw = data.get(field.get("id"))
        val = _fmt_field_value(field, raw)
        _render_kv_row(pdf, label, val)


def _render_narrative(pdf: FPDF, section: dict, data: dict) -> None:
    for field in section.get("fields") or []:
        raw = data.get(field.get("id"))
        val = str(raw or "").strip()[:8000]
        if not val:
            continue
        if field.get("label"):
            pdf.set_x(pdf.l_margin)
            pdf.set_font("Helvetica", "B", 10)
            pdf.write(5.5, field["label"] + ":")
            pdf.ln(5.5)
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 5.5, val)
        pdf.ln(1)


def _render_repeatable(pdf: FPDF, section: dict, data: dict) -> None:
    entries = (data or {}).get("entries") or []
    fields = section.get("fields") or []
    if not entries:
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(120, 120, 120)
        pdf.cell(0, 5, "(No entries recorded.)", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        return
    for i, entry in enumerate(entries, start=1):
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(0, 5, f"Entry #{i}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        for field in fields:
            label = field.get("label") or field.get("id", "field")
            raw = entry.get(field.get("id"))
            _render_kv_row(pdf, "  " + label, _fmt_field_value(field, raw))
        pdf.ln(1)


def _render_academic_grades(pdf: FPDF, section: dict, data: dict) -> None:
    subjects = section.get("subjects") or []
    grading = section.get("grading_system") or []
    if not subjects:
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(120, 120, 120)
        pdf.cell(0, 5, "(No subjects configured for this section.)", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        return
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(240, 240, 245)
    pdf.cell(65, 6, "Subject", border=1, fill=True)
    pdf.cell(25, 6, "Mark", border=1, fill=True, align="C")
    pdf.cell(25, 6, "Grade", border=1, fill=True, align="C")
    pdf.cell(0, 6, "Remarks", border=1, fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    for subject in subjects:
        sid = subject.get("id")
        sname = subject.get("name") or "Subject"
        total = subject.get("total_marks") or 100
        entry = (data or {}).get(sid, {}) if isinstance(data, dict) else {}
        marks = entry.get("marks_obtained")
        remarks = entry.get("remarks") or ""
        try:
            marks_num = float(marks) if marks is not None else None
        except (TypeError, ValueError):
            marks_num = None
        grade = _grade_for(marks_num, total, grading)
        pdf.cell(65, 5.5, str(sname)[:38], border=1)
        pdf.cell(25, 5.5, f"{marks}/{total}" if marks is not None else "-", border=1, align="C")
        pdf.cell(25, 5.5, grade or "-", border=1, align="C")
        pdf.cell(0, 5.5, str(remarks)[:60], border=1, new_x="LMARGIN", new_y="NEXT")


def _render_meals(pdf: FPDF, section: dict, data: dict) -> None:
    for field in section.get("fields") or []:
        label = field.get("label") or field.get("id", "meal")
        raw = data.get(field.get("id"))
        _render_kv_row(pdf, label, _fmt_field_value(field, raw))


def _render_info_display(pdf: FPDF, section: dict, data: dict) -> None:
    _render_checklist(pdf, section, data)


def _render_summary(pdf: FPDF, section: dict, data: dict) -> None:
    _render_checklist(pdf, section, data)


def _render_signatures(pdf: FPDF, section: dict, data: dict) -> None:
    for field in section.get("fields") or []:
        label = field.get("label") or "Signature"
        raw = data.get(field.get("id"))
        val = str(raw or "").strip()
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(60, 5.5, f"{label}:", new_x="RIGHT")
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 5.5, val or "_____________________", new_x="LMARGIN", new_y="NEXT")


def _render_generic(pdf: FPDF, section: dict, data: dict) -> None:
    if not data:
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(120, 120, 120)
        pdf.cell(0, 5, "(No data.)", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        return
    for field in section.get("fields") or []:
        label = field.get("label") or field.get("id", "field")
        raw = data.get(field.get("id"))
        _render_kv_row(pdf, label, _fmt_field_value(field, raw))


_SECTION_RENDERERS = {
    "CHECKLIST": _render_checklist,
    "NARRATIVE": _render_narrative,
    "REPEATABLE_ENTRIES": _render_repeatable,
    "ACADEMIC_GRADES": _render_academic_grades,
    "MEALS": _render_meals,
    "INFO_DISPLAY": _render_info_display,
    "SUMMARY": _render_summary,
    "SIGNATURES": _render_signatures,
}


def _fmt_field_value(field: dict, value: Any) -> str:
    if value is None or value == "":
        return "-"
    ftype = (field.get("type") or "").upper()
    if ftype == "CHECKBOX":
        return "Yes" if value else "No"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, dict):
        return ", ".join(f"{k}: {v}" for k, v in value.items())
    return str(value)


def _grade_for(marks: float | None, total: float | int, grading: list[dict]) -> str:
    if marks is None or not grading:
        return ""
    try:
        pct = (marks / float(total)) * 100 if total else 0
    except Exception:
        return ""
    for band in grading:
        try:
            lo = float(band.get("min", 0))
            hi = float(band.get("max", 100))
        except Exception:
            continue
        if lo <= pct <= hi:
            return str(band.get("grade") or "")
    return ""
