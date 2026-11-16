"""School calendar service — per-jurisdiction academic years, terms, and
public holidays.

Storage: a `system_settings` row per (country, year), keyed as
`calendar:<CC>:<YYYY>`. No new tables. The super admin edits the JSONB
directly through `/admin/school-calendars`; tenants read from it via
`get_term_for_date(country, date)` to label reports and align TERMLY
billing.

Why system_settings instead of dedicated tables:
  - Super admin can edit live; no migrations to add a new country.
  - JSONB shape is small and self-describing.
  - Ships with zero risk of orphan rows (no FKs across tenants).

Shape of the stored value::

    {
      "terms": [
        {"number": 1, "name": "Term 1", "start": "2026-01-13", "end": "2026-04-10"},
        ...
      ],
      "holidays": [
        {"name": "Independence Day", "date": "2026-04-18", "type": "PUBLIC"},
        ...
      ],
      "source": "MoPSE Circular 02 of 2026",
      "updated_by": "<user id>",
      "updated_at": "<ISO 8601>"
    }

The service never raises on malformed data — a bad row returns None from
``get_calendar`` and skips the resolver, so a corrupt JSONB can't 500 a
report render. The super admin edit form will re-validate on save.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_settings import SystemSettings

logger = logging.getLogger(__name__)

CALENDAR_KEY_PREFIX = "calendar:"


def _key(country_code: str, year: int) -> str:
    return f"{CALENDAR_KEY_PREFIX}{country_code.upper()}:{year}"


@dataclass(frozen=True)
class Term:
    number: int
    name: str
    start: date
    end: date

    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end


@dataclass(frozen=True)
class Holiday:
    name: str
    start: date
    end: date
    type: str = "PUBLIC"  # PUBLIC | SCHOOL_HOLIDAY | HALF_TERM


@dataclass(frozen=True)
class Calendar:
    country_code: str
    year: int
    terms: tuple[Term, ...]
    holidays: tuple[Holiday, ...]
    source: str = ""
    updated_by: str = ""
    updated_at: str = ""

    def term_for(self, d: date) -> Term | None:
        for t in self.terms:
            if t.contains(d):
                return t
        return None


def _parse_date(s: str) -> date | None:
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _calendar_from_row(country_code: str, year: int, value: dict) -> Calendar | None:
    """Parse one stored row. Returns None if required term fields are
    missing or unparseable — the caller treats that as "no calendar"."""
    raw_terms = value.get("terms") or []
    terms: list[Term] = []
    for t in raw_terms:
        start = _parse_date(t.get("start", ""))
        end = _parse_date(t.get("end", ""))
        if not (start and end):
            logger.warning(
                "calendar %s/%s term %s has unparseable dates; skipping",
                country_code, year, t.get("number"),
            )
            continue
        terms.append(Term(
            number=int(t.get("number", 0)),
            name=str(t.get("name") or f"Term {t.get('number', '?')}"),
            start=start,
            end=end,
        ))

    if not terms:
        return None

    raw_hols = value.get("holidays") or []
    hols: list[Holiday] = []
    for h in raw_hols:
        # Holidays can be single-day (`date`) or range (`start`/`end`).
        single = _parse_date(h.get("date", ""))
        start = _parse_date(h.get("start", "")) or single
        end = _parse_date(h.get("end", "")) or single
        if not (start and end):
            continue
        hols.append(Holiday(
            name=str(h.get("name") or ""),
            start=start,
            end=end,
            type=str(h.get("type") or "PUBLIC"),
        ))

    return Calendar(
        country_code=country_code.upper(),
        year=year,
        terms=tuple(sorted(terms, key=lambda t: t.start)),
        holidays=tuple(sorted(hols, key=lambda h: h.start)),
        source=str(value.get("source") or ""),
        updated_by=str(value.get("updated_by") or ""),
        updated_at=str(value.get("updated_at") or ""),
    )


async def get_calendar(
    db: AsyncSession, country_code: str, year: int
) -> Calendar | None:
    """Return the stored calendar, or None if the row is missing /
    malformed."""
    result = await db.execute(
        select(SystemSettings).where(SystemSettings.key == _key(country_code, year))
    )
    row = result.scalar_one_or_none()
    if row is None or not isinstance(row.value, dict):
        return None
    return _calendar_from_row(country_code, year, row.value)


async def list_calendars(db: AsyncSession) -> list[Calendar]:
    """Every (country, year) row currently stored — super admin listing."""
    result = await db.execute(
        select(SystemSettings).where(SystemSettings.key.like(f"{CALENDAR_KEY_PREFIX}%"))
    )
    out: list[Calendar] = []
    for row in result.scalars():
        parts = row.key.split(":")
        if len(parts) != 3:
            continue
        _, cc, yr_str = parts
        try:
            yr = int(yr_str)
        except ValueError:
            continue
        cal = _calendar_from_row(cc, yr, row.value or {})
        if cal is not None:
            out.append(cal)
    return sorted(out, key=lambda c: (c.country_code, c.year))


async def save_calendar(
    db: AsyncSession,
    country_code: str,
    year: int,
    terms: list[dict[str, Any]],
    holidays: list[dict[str, Any]],
    source: str = "",
    updated_by: str = "",
) -> Calendar:
    """Upsert a calendar. Validates by round-tripping through the parser —
    raises ValueError if the terms can't be read back."""
    country_code = country_code.upper()
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "terms": terms,
        "holidays": holidays,
        "source": source,
        "updated_by": updated_by,
        "updated_at": now,
    }

    # Validate shape before persisting.
    parsed = _calendar_from_row(country_code, year, payload)
    if parsed is None:
        raise ValueError("Calendar must have at least one term with valid dates")
    # Terms must not overlap.
    ts = sorted(parsed.terms, key=lambda t: t.start)
    for a, b in zip(ts, ts[1:]):
        if a.end >= b.start:
            raise ValueError(
                f"Term {a.number} ends on {a.end} which overlaps Term {b.number} "
                f"starting {b.start}"
            )

    key = _key(country_code, year)
    result = await db.execute(select(SystemSettings).where(SystemSettings.key == key))
    row = result.scalar_one_or_none()
    if row is None:
        row = SystemSettings(key=key, value=payload)
        db.add(row)
    else:
        row.value = payload
    await db.flush()
    return parsed


async def delete_calendar(db: AsyncSession, country_code: str, year: int) -> bool:
    """Remove a calendar. Returns False if nothing was there."""
    result = await db.execute(
        delete(SystemSettings).where(SystemSettings.key == _key(country_code, year))
    )
    return (result.rowcount or 0) > 0


async def copy_calendar(
    db: AsyncSession,
    country_code: str,
    from_year: int,
    to_year: int,
    updated_by: str = "",
) -> Calendar:
    """Shift every term + holiday forward (to_year - from_year) years.
    Convenience for "give me the 2027 draft from the 2026 dates" — the
    super admin then nudges the week-off differences and saves."""
    src = await get_calendar(db, country_code, from_year)
    if src is None:
        raise ValueError(f"No calendar to copy from for {country_code} {from_year}")

    def shift(d: date) -> str:
        # replace() can fail for Feb 29 → non-leap. Fall back to Feb 28.
        try:
            return d.replace(year=d.year + (to_year - from_year)).isoformat()
        except ValueError:
            return d.replace(month=2, day=28, year=d.year + (to_year - from_year)).isoformat()

    new_terms = [
        {"number": t.number, "name": t.name, "start": shift(t.start), "end": shift(t.end)}
        for t in src.terms
    ]
    new_holidays = [
        {"name": h.name, "start": shift(h.start), "end": shift(h.end), "type": h.type}
        for h in src.holidays
    ]
    return await save_calendar(
        db, country_code, to_year, new_terms, new_holidays,
        source=f"Copied from {from_year} — review dates before relying on this",
        updated_by=updated_by,
    )


async def get_term_for_date(
    db: AsyncSession, country_code: str, d: date
) -> Term | None:
    """Resolver used by billing and report cards. Returns None if there's
    no calendar for that year or the date isn't inside any term."""
    cal = await get_calendar(db, country_code, d.year)
    if cal is None:
        return None
    return cal.term_for(d)


async def countries_missing_upcoming_year(
    db: AsyncSession,
    active_country_codes: set[str],
    today: date | None = None,
    horizon_days: int = 90,
) -> list[str]:
    """Which countries have active tenants but no calendar for next year
    yet? Used by the super admin dashboard banner. Only fires inside the
    last ``horizon_days`` of the current year, so most of the time the
    banner is quiet.

    ``active_country_codes`` is the set of countries with at least one
    live tenant — the caller supplies this (jurisdiction_service knows
    it)."""
    today = today or date.today()
    end_of_year = date(today.year, 12, 31)
    if (end_of_year - today).days > horizon_days:
        return []

    missing: list[str] = []
    for cc in sorted(active_country_codes):
        next_year_cal = await get_calendar(db, cc, today.year + 1)
        if next_year_cal is None:
            missing.append(cc.upper())
    return missing


# ---------------------------------------------------------------------------
# Jurisdiction seeds — baked-in starting data the super admin can edit.
# These are approximate; MoPSE publishes exact dates per year and the
# super admin refines them. Shipping the seed lets a fresh install show
# "Zimbabwe 2026" ready on the first visit.
# ---------------------------------------------------------------------------

ZIMBABWE_2026_SEED = {
    "terms": [
        {"number": 1, "name": "Term 1", "start": "2026-01-13", "end": "2026-04-10"},
        {"number": 2, "name": "Term 2", "start": "2026-05-05", "end": "2026-08-07"},
        {"number": 3, "name": "Term 3", "start": "2026-09-08", "end": "2026-12-04"},
    ],
    "holidays": [
        {"name": "New Year's Day", "date": "2026-01-01", "type": "PUBLIC"},
        {"name": "Good Friday", "date": "2026-04-03", "type": "PUBLIC"},
        {"name": "Easter Saturday", "date": "2026-04-04", "type": "PUBLIC"},
        {"name": "Easter Monday", "date": "2026-04-06", "type": "PUBLIC"},
        {"name": "Independence Day", "date": "2026-04-18", "type": "PUBLIC"},
        {"name": "Workers' Day", "date": "2026-05-01", "type": "PUBLIC"},
        {"name": "Africa Day", "date": "2026-05-25", "type": "PUBLIC"},
        {"name": "Heroes' Day", "date": "2026-08-10", "type": "PUBLIC"},
        {"name": "Defence Forces Day", "date": "2026-08-11", "type": "PUBLIC"},
        {"name": "Unity Day", "date": "2026-12-22", "type": "PUBLIC"},
        {"name": "Christmas Day", "date": "2026-12-25", "type": "PUBLIC"},
        {"name": "Boxing Day", "date": "2026-12-26", "type": "PUBLIC"},
    ],
    "source": "MoPSE calendar — draft, super admin please confirm against the official circular",
}


async def seed_zimbabwe_2026_if_missing(db: AsyncSession) -> bool:
    """One-shot seed. Returns True if the row was created, False if it
    already existed (so callers don't double-commit)."""
    existing = await get_calendar(db, "ZW", 2026)
    if existing is not None:
        return False
    await save_calendar(
        db,
        country_code="ZW",
        year=2026,
        terms=ZIMBABWE_2026_SEED["terms"],
        holidays=ZIMBABWE_2026_SEED["holidays"],
        source=ZIMBABWE_2026_SEED["source"],
        updated_by="system-seed",
    )
    return True
