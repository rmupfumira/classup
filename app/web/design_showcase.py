"""Design showcase — four full-page prototypes of the Exams page.

Lives behind /design-showcase. Each prototype at /design-showcase/{N}
is a standalone HTML page (its own <html>, not extending base.html)
so the prototype's styling is fully isolated from the production
sidebar/navbar. Every design is driven by the same sample data in
``_sample_exams()`` so the comparison is apples-to-apples.

The production Exams page at /exams is untouched. When the user picks
a direction this route is retired (or kept as a design playground).
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.templates_config import templates

router = APIRouter(prefix="/design-showcase")


@dataclass(frozen=True)
class SampleExam:
    id: str
    name: str
    class_name: str
    grade_level: str
    academic_year: str
    term: int
    status: str  # SCHEDULED | IN_PROGRESS | FINALIZED | DRAFT
    papers_count: int
    subjects_count: int
    grading_system: str
    created_at: str          # human-readable
    scheduled_start: str | None
    marked_pct: int          # 0..100 progress bar
    student_count: int


def _sample_exams() -> list[SampleExam]:
    """Consistent sample fixture — same rows for every design so the
    comparison is about styling, not content."""
    return [
        SampleExam(
            id="exam-01",
            name="End of Term 3 Exam",
            class_name="Form 3A",
            grade_level="Form 3",
            academic_year="2026",
            term=3,
            status="FINALIZED",
            papers_count=11,
            subjects_count=11,
            grading_system="ZIMSEC O-Level",
            created_at="Nov 19, 2026",
            scheduled_start="Dec 01 · 09:00",
            marked_pct=100,
            student_count=12,
        ),
        SampleExam(
            id="exam-02",
            name="Mid-Term Assessment",
            class_name="Form 2B",
            grade_level="Form 2",
            academic_year="2026",
            term=3,
            status="IN_PROGRESS",
            papers_count=10,
            subjects_count=10,
            grading_system="ZIMSEC O-Level",
            created_at="Nov 12, 2026",
            scheduled_start="Nov 25 · 10:00",
            marked_pct=64,
            student_count=15,
        ),
        SampleExam(
            id="exam-03",
            name="Weight Rebalance Test",
            class_name="Form 1B",
            grade_level="Form 1",
            academic_year="2026",
            term=2,
            status="SCHEDULED",
            papers_count=9,
            subjects_count=9,
            grading_system="ZIMSEC O-Level",
            created_at="Nov 20, 2026",
            scheduled_start="Dec 08 · 09:00",
            marked_pct=0,
            student_count=12,
        ),
        SampleExam(
            id="exam-04",
            name="Local Smoke Test Exam",
            class_name="Form 3A",
            grade_level="Form 3",
            academic_year="2026",
            term=3,
            status="FINALIZED",
            papers_count=10,
            subjects_count=10,
            grading_system="ZIMSEC O-Level",
            created_at="Nov 15, 2026",
            scheduled_start="Nov 22 · 09:00",
            marked_pct=100,
            student_count=12,
        ),
        SampleExam(
            id="exam-05",
            name="Term 1 Baseline",
            class_name="Form 4A",
            grade_level="Form 4",
            academic_year="2026",
            term=1,
            status="DRAFT",
            papers_count=0,
            subjects_count=10,
            grading_system="Cambridge IGCSE",
            created_at="Nov 03, 2026",
            scheduled_start=None,
            marked_pct=0,
            student_count=14,
        ),
    ]


DESIGNS = [
    {
        "id": "01",
        "slug": "minimalist",
        "name": "Premium Minimalist",
        "tagline": "Sparse, confident, whitespace-first",
        "hint": "Think Linear, Stripe, Notion",
        "template": "design_showcase/d01_minimalist.html",
    },
    {
        "id": "02",
        "slug": "enterprise",
        "name": "Modern Enterprise",
        "tagline": "Dense data, decisive signals, power-user ready",
        "hint": "Think Datadog, Salesforce",
        "template": "design_showcase/d02_enterprise.html",
    },
    {
        "id": "03",
        "slug": "editorial",
        "name": "Contemporary Editorial",
        "tagline": "Large type, offset grid, print-magazine confident",
        "hint": "Think Framer sites, The New Yorker × SaaS",
        "template": "design_showcase/d03_editorial.html",
    },
    {
        "id": "04",
        "slug": "soft",
        "name": "Soft Modern",
        "tagline": "Rounded, friendly, warmer navy with soft shadows",
        "hint": "Think Headspace × SaaS, iOS-friendly",
        "template": "design_showcase/d04_soft.html",
    },
]


def _ctx(request: Request):
    """Shared context: sample data + the full design catalogue so a
    design page can link to its siblings."""
    return {
        "request": request,
        "exams": _sample_exams(),
        "designs": DESIGNS,
    }


@router.get("", response_class=HTMLResponse)
async def showcase_index(request: Request):
    """Landing — side-by-side selector for the four designs."""
    return templates.TemplateResponse(
        "design_showcase/index.html", _ctx(request),
    )


@router.get("/{design_id}", response_class=HTMLResponse)
async def showcase_design(request: Request, design_id: str):
    """Render the Exams page in the chosen design."""
    design = next((d for d in DESIGNS if d["id"] == design_id), None)
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")
    ctx = _ctx(request)
    ctx["design"] = design
    return templates.TemplateResponse(design["template"], ctx)
