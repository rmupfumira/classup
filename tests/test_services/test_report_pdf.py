"""Tests for report_pdf — the fpdf2 renderer that turns a DailyReport
into PDF bytes for WhatsApp delivery.

Focused on the failure modes that matter:
  - Cursor drift across cell/multi_cell boundaries (this is what caused
    the "Not enough horizontal space to render a single character"
    exception in the naps section in production)
  - Long values and unicode don't blow up
  - Every documented section type renders without raising
  - A broken section doesn't abort the whole PDF (the per-section
    try/except in render_report_pdf is the safety net)

Renders are validated by (a) not raising, (b) producing non-empty
bytes, and (c) starting with the PDF magic bytes %PDF. We don't
diff the visual output — that's what a rendering-in-CI harness would
do, and it's overkill for a WhatsApp-facing PDF.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.report_pdf import render_report_pdf


def _mk_report(
    *,
    sections: list[dict],
    section_data: dict,
    student_name: tuple[str, str] = ("Aaron", "Moyo"),
    class_name: str = "Grade 3B",
    template_name: str = "Daily Report",
    report_type: str = "DAILY_ACTIVITY",
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
    )


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
