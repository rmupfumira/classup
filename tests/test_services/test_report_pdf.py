"""Tests for report_pdf — turns a DailyReport into PDF bytes for
WhatsApp delivery.

Two rendering paths exercised:
  - Primary: WeasyPrint (renders app/templates/reports/pdf.html — the
    high-fidelity PDF that mirrors the web view). Skipped when the
    weasyprint import fails (Windows dev without libpango, mostly).
  - Fallback: fpdf2 pure-Python renderer, still used on dev boxes where
    WeasyPrint can't load. The invariants below (returns %PDF bytes,
    doesn't crash on weird data) must hold for BOTH paths.

Focused on the failure modes that matter:
  - Cursor drift across cell/multi_cell boundaries (caused
    "Not enough horizontal space to render a single character"
    in the fpdf2 naps section in production)
  - Long values and unicode don't blow up
  - Every documented section type renders without raising
  - A broken section doesn't abort the whole PDF (the per-section
    try/except in the fpdf2 renderer is the safety net)

Renders are validated by (a) not raising, (b) producing non-empty
bytes, and (c) starting with the PDF magic bytes %PDF. We don't
diff visual output — that's what a rendering-in-CI harness would do,
and it's overkill for a WhatsApp-facing PDF.
"""

from __future__ import annotations

import importlib.util
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import report_pdf
from app.services.report_pdf import render_report_pdf


HAS_WEASYPRINT = importlib.util.find_spec("weasyprint") is not None


def _mk_report(
    *,
    sections: list[dict],
    section_data: dict,
    student_name: tuple[str, str] = ("Aaron", "Moyo"),
    class_name: str = "Grade 3B",
    template_name: str = "Daily Report",
    report_type: str = "DAILY_ACTIVITY",
    status: str = "FINALIZED",
):
    """Fake DailyReport that walks like the real one for the renderer.
    Uses SimpleNamespaces so no DB round-trip needed."""
    template = SimpleNamespace(
        name=template_name,
        report_type=report_type,
        sections=sections,
    )
    student = SimpleNamespace(
        first_name=student_name[0],
        last_name=student_name[1],
    )
    school_class = SimpleNamespace(name=class_name)
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=None,  # skips tenant lookup — renderer defaults to "ClassUp"
        report_date=date(2026, 9, 10),
        finalized_at=datetime(2026, 9, 10, 15, 30, tzinfo=timezone.utc),
        report_data={"sections": section_data},
        template=template,
        student=student,
        school_class=school_class,
        status=status,
        created_by_user=SimpleNamespace(first_name="Ms.", last_name="Dube"),
    )


@pytest.fixture(autouse=True)
def _reset_weasyprint_cache():
    """The renderer caches its WeasyPrint probe at module level. Tests
    that monkeypatch the probe need a clean slate afterwards, otherwise
    a later test still sees the mocked state."""
    report_pdf._weasyprint_available = None
    report_pdf._jinja_env = None
    yield
    report_pdf._weasyprint_available = None
    report_pdf._jinja_env = None


@pytest.fixture
def force_fpdf(monkeypatch):
    """Force the fpdf2 fallback path, regardless of whether WeasyPrint
    is importable in this environment. Every fpdf2-invariant test uses
    this — the tests exercise the fallback specifically."""
    monkeypatch.setattr(report_pdf, "_try_import_weasyprint", lambda: None)


@pytest.mark.usefixtures("force_fpdf")
class TestRepeatableEntries:
    """The section that exploded in production."""

    async def test_naps_section_renders_multiple_entries(self):
        """The exact shape a daycare naps section has: one field per
        entry, multiple entries in a day. This USED to trigger
        FPDFException from cursor drift across the multi_cell calls."""
        section = {
            "id": "naps",
            "title": "Nap tracking",
            "type": "REPEATABLE_ENTRIES",
            "display_order": 1,
            "fields": [
                {"id": "start_time", "label": "Start time", "type": "TIME"},
                {"id": "end_time",   "label": "End time",   "type": "TIME"},
                {"id": "notes",      "label": "Notes",      "type": "TEXT"},
            ],
        }
        data = {
            "naps": {
                "entries": [
                    {"start_time": "09:30", "end_time": "10:15", "notes": "Fell asleep quickly, calm nap"},
                    {"start_time": "12:45", "end_time": "13:30", "notes": "Woke briefly, back to sleep"},
                    {"start_time": "15:00", "end_time": "15:20", "notes": "Short cat-nap"},
                ]
            }
        }
        report = _mk_report(sections=[section], section_data=data)

        db = AsyncMock()  # tenant_id is None so db is unused
        pdf_bytes = await render_report_pdf(db, report)
        assert isinstance(pdf_bytes, bytes)
        assert pdf_bytes.startswith(b"%PDF")
        assert len(pdf_bytes) > 500  # non-trivial content

    async def test_empty_repeatable_section_renders(self):
        section = {
            "id": "meals",
            "title": "Meals",
            "type": "REPEATABLE_ENTRIES",
            "display_order": 1,
            "fields": [{"id": "meal", "label": "Meal", "type": "TEXT"}],
        }
        # No entries key at all — must not crash, should show "no entries".
        report = _mk_report(sections=[section], section_data={"meals": {}})
        pdf_bytes = await render_report_pdf(AsyncMock(), report)
        assert pdf_bytes.startswith(b"%PDF")


@pytest.mark.usefixtures("force_fpdf")
class TestAllSectionTypes:
    """Every documented section type must render without raising."""

    @pytest.mark.parametrize("sec_type,extra", [
        ("CHECKLIST", {}),
        ("NARRATIVE", {}),
        ("MEALS", {}),
        ("INFO_DISPLAY", {}),
        ("SUMMARY", {}),
        ("SIGNATURES", {}),
    ])
    async def test_section_type_renders(self, sec_type, extra):
        section = {
            "id": "s1",
            "title": f"{sec_type} test",
            "type": sec_type,
            "display_order": 1,
            "fields": [
                {"id": "f1", "label": "Field 1", "type": "TEXT"},
                {"id": "f2", "label": "Field 2 with a fairly long label", "type": "TEXTAREA"},
            ],
            **extra,
        }
        data = {"s1": {"f1": "value one", "f2": "value two with more content"}}
        report = _mk_report(sections=[section], section_data=data)
        pdf_bytes = await render_report_pdf(AsyncMock(), report)
        assert pdf_bytes.startswith(b"%PDF")

    async def test_academic_grades_section_renders(self):
        section = {
            "id": "grades",
            "title": "Term 2 Grades",
            "type": "ACADEMIC_GRADES",
            "display_order": 1,
            "subjects": [
                {"id": "math", "name": "Mathematics", "total_marks": 100},
                {"id": "eng",  "name": "English",     "total_marks": 100},
                {"id": "sci",  "name": "Science",     "total_marks": 100},
            ],
            "grading_system": [
                {"min": 80, "max": 100, "grade": "A"},
                {"min": 60, "max": 79,  "grade": "B"},
                {"min": 0,  "max": 59,  "grade": "C"},
            ],
        }
        data = {
            "grades": {
                "math": {"marks_obtained": 85, "remarks": "Excellent"},
                "eng":  {"marks_obtained": 72, "remarks": "Good progress"},
                "sci":  {"marks_obtained": 55, "remarks": "Needs practice"},
            }
        }
        report = _mk_report(sections=[section], section_data=data)
        pdf_bytes = await render_report_pdf(AsyncMock(), report)
        assert pdf_bytes.startswith(b"%PDF")


@pytest.mark.usefixtures("force_fpdf")
class TestErrorRecovery:
    """A broken section must not abort the whole PDF."""

    async def test_unknown_section_type_falls_through_to_generic(self):
        section = {
            "id": "weird",
            "title": "Some new section",
            "type": "PROBABLY_A_NEW_TYPE",
            "display_order": 1,
            "fields": [{"id": "f", "label": "Field", "type": "TEXT"}],
        }
        report = _mk_report(sections=[section], section_data={"weird": {"f": "hi"}})
        pdf_bytes = await render_report_pdf(AsyncMock(), report)
        # Generic renderer catches the fall-through; PDF still emits.
        assert pdf_bytes.startswith(b"%PDF")

    async def test_extremely_long_field_value_gets_truncated(self):
        """A pathological 100KB value in a single field must not hang the
        renderer or produce a broken PDF. The _render_kv_row truncates at
        2000 chars."""
        section = {
            "id": "s",
            "title": "Long value test",
            "type": "CHECKLIST",
            "display_order": 1,
            "fields": [{"id": "f", "label": "F", "type": "TEXT"}],
        }
        data = {"s": {"f": "x" * 100_000}}
        report = _mk_report(sections=[section], section_data=data)
        pdf_bytes = await render_report_pdf(AsyncMock(), report)
        assert pdf_bytes.startswith(b"%PDF")
        # The value was truncated; PDF stays under a couple hundred KB
        # (would be ~800KB+ if we serialized the whole 100k string).
        assert len(pdf_bytes) < 500_000

    async def test_no_template_still_produces_pdf(self):
        """A report with no template shouldn't crash — the caller might
        pass a report that predates its template being loaded."""
        report = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=None,
            report_date=date(2026, 9, 10),
            finalized_at=None,
            report_data={"sections": {}},
            template=None,
            student=SimpleNamespace(first_name="X", last_name="Y"),
            school_class=SimpleNamespace(name="Class A"),
        )
        pdf_bytes = await render_report_pdf(AsyncMock(), report)
        assert pdf_bytes.startswith(b"%PDF")


@pytest.mark.usefixtures("force_fpdf")
class TestLongLabels:
    """Cursor drift is more likely with long labels; make sure they don't
    push the value column off the page."""

    async def test_very_long_field_label(self):
        section = {
            "id": "s",
            "title": "Long-label test",
            "type": "CHECKLIST",
            "display_order": 1,
            "fields": [{
                "id": "f",
                "label": "A ridiculously long field label that admins sometimes create in the template editor "
                         "because they didn't realise it would push the layout around",
                "type": "TEXT",
            }],
        }
        data = {"s": {"f": "the value"}}
        report = _mk_report(sections=[section], section_data=data)
        pdf_bytes = await render_report_pdf(AsyncMock(), report)
        assert pdf_bytes.startswith(b"%PDF")


@pytest.mark.skipif(
    not HAS_WEASYPRINT,
    reason="weasyprint not installed (Windows dev without libpango is fine — prod Docker has it)",
)
class TestWeasyPrintPath:
    """Primary render path. Runs only where WeasyPrint's system deps are
    installed — never fails in a way that would say the fallback is
    broken. If find_spec() finds weasyprint but the import still blows
    up at runtime (missing native lib), _try_import_weasyprint() catches
    it and render_report_pdf() falls back — so these tests still yield a
    valid PDF, just via the fpdf2 code path."""

    async def test_full_daycare_report_renders_via_weasyprint(self):
        """A realistic daycare daily report — mixed section types, the
        naps/fluids/bathroom special-cased blocks, and a checklist —
        exercises the pdf.html template end-to-end."""
        sections = [
            {
                "id": "naps",
                "title": "Naps",
                "type": "REPEATABLE_ENTRIES",
                "display_order": 1,
                "color": "violet",
                "fields": [
                    {"id": "start_time", "label": "Start", "type": "TIME"},
                    {"id": "end_time",   "label": "End",   "type": "TIME"},
                ],
            },
            {
                "id": "fluids",
                "title": "Fluids",
                "type": "REPEATABLE_ENTRIES",
                "display_order": 2,
                "color": "blue",
                "fields": [
                    {"id": "time",   "label": "Time",   "type": "TIME"},
                    {"id": "amount", "label": "Amount", "type": "TEXT"},
                    {"id": "type",   "label": "Type",   "type": "TEXT"},
                ],
            },
            {
                "id": "activities",
                "title": "Activities",
                "type": "CHECKLIST",
                "display_order": 3,
                "color": "green",
                "fields": [
                    {"id": "painting", "label": "Painting", "type": "CHECKBOX"},
                    {"id": "reading",  "label": "Reading",  "type": "CHECKBOX"},
                    {"id": "outdoor",  "label": "Outdoor play notes", "type": "TEXTAREA"},
                ],
            },
            {
                "id": "narrative",
                "title": "How my day went",
                "type": "NARRATIVE",
                "display_order": 4,
                "color": "orange",
                "fields": [{"id": "story", "label": "", "type": "TEXTAREA"}],
            },
        ]
        data = {
            "naps": {"entries": [
                {"start_time": "10:00", "end_time": "10:45", "duration": "45m"},
                {"start_time": "13:30", "end_time": "14:20", "duration": "50m"},
            ]},
            "fluids": {"entries": [
                {"time": "09:15", "amount": "150ml", "type": "Water", "notes": "Drank it all"},
                {"time": "12:00", "amount": "200ml", "type": "Milk"},
            ]},
            "activities": {
                "painting": True,
                "reading": True,
                "outdoor": "Loved the sandpit today",
            },
            "narrative": {"story": "Aaron had a lovely, calm day. Played happily with friends."},
            "notes": {"content": "Please pack an extra jumper for tomorrow — cold snap forecast."},
        }
        report = _mk_report(sections=sections, section_data=data)
        pdf_bytes = await render_report_pdf(AsyncMock(), report)
        assert pdf_bytes.startswith(b"%PDF")
        # WeasyPrint output for a real report is meaningfully bigger
        # than an empty fpdf shell — sanity-check we're not silently
        # producing a stub.
        assert len(pdf_bytes) > 2000

    async def test_academic_grades_table_renders_via_weasyprint(self):
        """Report card path — the ACADEMIC_GRADES branch renders as an
        HTML table with grade lookup, which is more complex than the
        daycare path."""
        sections = [{
            "id": "grades",
            "title": "Term 2 Report Card",
            "type": "ACADEMIC_GRADES",
            "display_order": 1,
            "color": "blue",
            "subjects": [
                {"id": "math", "name": "Mathematics", "total_marks": 100},
                {"id": "eng",  "name": "English",     "total_marks": 100},
                {"id": "sci",  "name": "Science",     "total_marks": 100},
            ],
            "grading_system": [
                {"min": 80, "max": 100, "grade": "A"},
                {"min": 60, "max": 79,  "grade": "B"},
                {"min": 0,  "max": 59,  "grade": "C"},
            ],
        }]
        data = {"grades": {
            "math": {"marks_obtained": 92, "remarks": "Outstanding"},
            "eng":  {"marks_obtained": 74, "remarks": "Good"},
            "sci":  {"marks_obtained": 58, "remarks": "Needs revision"},
        }}
        report = _mk_report(sections=sections, section_data=data)
        pdf_bytes = await render_report_pdf(AsyncMock(), report)
        assert pdf_bytes.startswith(b"%PDF")

    async def test_empty_report_still_renders_via_weasyprint(self):
        """A report with no sections and no data must still emit a PDF —
        WeasyPrint gets an almost-empty document but the header block
        keeps it valid."""
        report = _mk_report(sections=[], section_data={})
        pdf_bytes = await render_report_pdf(AsyncMock(), report)
        assert pdf_bytes.startswith(b"%PDF")


class TestFallbackBehavior:
    """When WeasyPrint fails at runtime, we drop to fpdf2 silently — the
    caller still gets a valid PDF."""

    async def test_weasyprint_import_failure_falls_back_to_fpdf(self, monkeypatch):
        """If _try_import_weasyprint returns None (import broken), the
        function still produces a PDF via fpdf2."""
        monkeypatch.setattr(report_pdf, "_try_import_weasyprint", lambda: None)
        section = {
            "id": "s", "title": "Test", "type": "CHECKLIST",
            "display_order": 1,
            "fields": [{"id": "f", "label": "Field", "type": "TEXT"}],
        }
        report = _mk_report(sections=[section], section_data={"s": {"f": "value"}})
        pdf_bytes = await render_report_pdf(AsyncMock(), report)
        assert pdf_bytes.startswith(b"%PDF")

    async def test_weasyprint_runtime_error_falls_back_to_fpdf(self, monkeypatch):
        """Even if WeasyPrint IS importable but blows up mid-render (bad
        HTML, missing font, malformed CSS), the fpdf2 fallback runs."""
        class _FakeWeasyprintModule:
            @staticmethod
            def HTML(**_kwargs):
                raise RuntimeError("simulated pango failure")

        monkeypatch.setattr(
            report_pdf, "_try_import_weasyprint",
            lambda: _FakeWeasyprintModule,
        )
        section = {
            "id": "s", "title": "Test", "type": "NARRATIVE",
            "display_order": 1,
            "fields": [{"id": "story", "label": "", "type": "TEXTAREA"}],
        }
        report = _mk_report(
            sections=[section],
            section_data={"s": {"story": "A quiet day."}},
        )
        pdf_bytes = await render_report_pdf(AsyncMock(), report)
        assert pdf_bytes.startswith(b"%PDF")


class TestSectionColorHelper:
    """The color mapping in pdf.html goes through _section_color_class.
    Its job is to survive whatever admins put in the template editor —
    Tailwind names, uppercase, hex codes, garbage — without crashing."""

    @pytest.mark.parametrize("color,expected", [
        ("violet", "color-violet"),
        ("purple", "color-violet"),  # legacy name — same bucket
        ("green",  "color-green"),
        ("#059669", "color-green"),
        ("orange", "color-orange"),
        ("blue",   "color-blue"),
        ("#2563EB", "color-blue"),   # uppercase hex
        ("red",    "color-red"),
        ("",       "color-violet"),  # empty falls through to default
        (None,     "color-violet"),  # None too
        ("chartreuse", "color-violet"),  # unknown → default
    ])
    def test_color_maps_to_expected_class(self, color, expected):
        assert report_pdf._section_color_class(color) == expected
