"""Academic term configuration — term-based attendance aggregation.

Terms live inside ``tenant.settings.academic_terms`` as a JSONB array::

    [{"id": "t1-2026", "number": 1, "year": 2026,
      "label": "Term 1 2026",
      "start": "2026-01-15", "end": "2026-04-02"}, ...]

Owner directive 2026-10-06: report cards should pre-fill the
academic context (term + year) and attendance totals (days present /
absent / late / excused) for the chosen term, so teachers don't
re-count every term.

Why JSONB and not a dedicated table?
- Terms are small, slow-changing data (~3-4 per year).
- They never need to be joined to by anything other than the tenant.
- A new table + migration + separate CRUD endpoints is more plumbing
  than the problem needs.

The compromise: one small service for CRUD + attendance aggregation,
reads/writes the JSONB in one place.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models import AttendanceRecord, Tenant


@dataclass(frozen=True)
class AcademicTerm:
    """One academic term."""

    id: str  # Stable short id, e.g. "t1-2026"
    number: int  # 1, 2, 3, 4
    year: int  # 2026
    label: str  # Display label, e.g. "Term 1 2026"
    start: date
    end: date

    @classmethod
    def from_dict(cls, d: dict) -> "AcademicTerm":
        return cls(
            id=str(d.get("id") or f"t{d.get('number', 1)}-{d.get('year', 2026)}"),
            number=int(d.get("number", 1)),
            year=int(d.get("year", 2026)),
            label=str(d.get("label") or f"Term {d.get('number', 1)} {d.get('year', 2026)}"),
            start=_parse_date(d.get("start")),
            end=_parse_date(d.get("end")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "number": self.number,
            "year": self.year,
            "label": self.label,
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
        }


def _parse_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v))
    except (ValueError, TypeError):
        return None


async def list_terms(db: AsyncSession, tenant_id: uuid.UUID) -> list[AcademicTerm]:
    """Return every term configured for this tenant, newest-first."""
    tenant = await db.get(Tenant, tenant_id)
    if not tenant or not tenant.settings:
        return []
    raw = (tenant.settings or {}).get("academic_terms") or []
    terms = [AcademicTerm.from_dict(d) for d in raw if isinstance(d, dict)]
    # Sort by year desc, number desc so the current term tends to be at the top.
    terms.sort(key=lambda t: (t.year, t.number), reverse=True)
    return terms


async def get_term(db: AsyncSession, tenant_id: uuid.UUID, term_id: str) -> AcademicTerm | None:
    """Fetch one term by id."""
    for t in await list_terms(db, tenant_id):
        if t.id == term_id:
            return t
    return None


async def upsert_term(
    db: AsyncSession, tenant_id: uuid.UUID, term: AcademicTerm
) -> AcademicTerm:
    """Add or replace a term by id. Validates start < end."""
    if not term.start or not term.end:
        raise ValueError("Term start and end dates are required")
    if term.start >= term.end:
        raise ValueError("Term start date must be before end date")

    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        raise ValueError(f"Tenant {tenant_id} not found")
    settings = dict(tenant.settings or {})
    terms_raw = list(settings.get("academic_terms") or [])
    # Replace matching id, else append.
    replaced = False
    for i, d in enumerate(terms_raw):
        if isinstance(d, dict) and d.get("id") == term.id:
            terms_raw[i] = term.to_dict()
            replaced = True
            break
    if not replaced:
        terms_raw.append(term.to_dict())
    settings["academic_terms"] = terms_raw
    tenant.settings = settings
    flag_modified(tenant, "settings")
    await db.commit()
    return term


async def delete_term(
    db: AsyncSession, tenant_id: uuid.UUID, term_id: str
) -> bool:
    """Remove a term by id. Returns True if a term was removed."""
    tenant = await db.get(Tenant, tenant_id)
    if not tenant or not tenant.settings:
        return False
    settings = dict(tenant.settings or {})
    terms_raw = list(settings.get("academic_terms") or [])
    before = len(terms_raw)
    terms_raw = [d for d in terms_raw if not (isinstance(d, dict) and d.get("id") == term_id)]
    if len(terms_raw) == before:
        return False
    settings["academic_terms"] = terms_raw
    tenant.settings = settings
    flag_modified(tenant, "settings")
    await db.commit()
    return True


# ─────────────── Attendance aggregation ───────────────


@dataclass(frozen=True)
class AttendanceSummary:
    """Attendance totals over a date range (inclusive both ends)."""

    present: int = 0
    absent: int = 0
    late: int = 0
    excused: int = 0
    total: int = 0  # Count of attendance_records in the range (all statuses)

    def to_dict(self) -> dict[str, int]:
        return {
            "present": self.present,
            "absent": self.absent,
            "late": self.late,
            "excused": self.excused,
            "total": self.total,
        }


async def get_attendance_summary(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    student_id: uuid.UUID,
    *,
    start: date,
    end: date,
) -> AttendanceSummary:
    """Count attendance_records per status for one student in a range.

    Tenant-scoped — never return another tenant's attendance even if
    a stray student_id slips through.
    """
    stmt = (
        select(AttendanceRecord.status, func.count(AttendanceRecord.id))
        .where(
            AttendanceRecord.tenant_id == tenant_id,
            AttendanceRecord.student_id == student_id,
            AttendanceRecord.date >= start,
            AttendanceRecord.date <= end,
        )
        .group_by(AttendanceRecord.status)
    )
    rows = (await db.execute(stmt)).all()
    counts = {row[0]: int(row[1]) for row in rows}
    return AttendanceSummary(
        present=counts.get("PRESENT", 0),
        absent=counts.get("ABSENT", 0),
        late=counts.get("LATE", 0),
        excused=counts.get("EXCUSED", 0),
        total=sum(counts.values()),
    )


async def term_report_context(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    student_id: uuid.UUID,
    term_id: str,
) -> dict[str, Any] | None:
    """Produce the auto-populate payload for a report card's term section.

    Returns a dict suitable for merging into ``report_data`` (or
    stashing as ``report_data['_term_summary']``). None if the term
    doesn't exist on this tenant.
    """
    term = await get_term(db, tenant_id, term_id)
    if not term:
        return None
    summary = await get_attendance_summary(
        db, tenant_id, student_id, start=term.start, end=term.end
    )
    return {
        "term_id": term.id,
        "term_number": term.number,
        "academic_year": term.year,
        "term_label": term.label,
        "term_start": term.start.isoformat(),
        "term_end": term.end.isoformat(),
        "attendance": summary.to_dict(),
    }
