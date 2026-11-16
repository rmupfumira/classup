"""Tests for calendar_service — per-jurisdiction academic years + terms."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_settings import SystemSettings
from app.services import calendar_service


@pytest.fixture
async def _wipe_calendars(db: AsyncSession):
    """Keep tests independent — wipe any calendar rows this test's codes
    might have left behind on a previous run."""
    yield
    await db.execute(
        delete(SystemSettings).where(
            SystemSettings.key.like(f"{calendar_service.CALENDAR_KEY_PREFIX}%TEST:%")
        )
    )
    await db.commit()


@pytest.mark.asyncio
async def test_save_and_get_roundtrips(db: AsyncSession, _wipe_calendars):
    """A saved calendar comes back with the same terms and holidays."""
    await calendar_service.save_calendar(
        db, "TEST", 2026,
        terms=[
            {"number": 1, "name": "Term 1", "start": "2026-01-13", "end": "2026-04-10"},
            {"number": 2, "name": "Term 2", "start": "2026-05-05", "end": "2026-08-07"},
        ],
        holidays=[{"name": "Workers' Day", "date": "2026-05-01", "type": "PUBLIC"}],
        source="unit test",
        updated_by="tester",
    )
    await db.commit()

    cal = await calendar_service.get_calendar(db, "TEST", 2026)
    assert cal is not None
    assert cal.country_code == "TEST"
    assert cal.year == 2026
    assert len(cal.terms) == 2
    assert cal.terms[0].start == date(2026, 1, 13)
    assert cal.terms[1].name == "Term 2"
    assert len(cal.holidays) == 1
    assert cal.holidays[0].name == "Workers' Day"


@pytest.mark.asyncio
async def test_term_for_date_resolves_correctly(db: AsyncSession, _wipe_calendars):
    """The resolver returns the right term for a date inside and the gap
    between terms returns None."""
    await calendar_service.save_calendar(
        db, "TEST", 2026,
        terms=[
            {"number": 1, "name": "Term 1", "start": "2026-01-13", "end": "2026-04-10"},
            {"number": 2, "name": "Term 2", "start": "2026-05-05", "end": "2026-08-07"},
        ],
        holidays=[],
    )
    await db.commit()

    # Inside Term 1
    t = await calendar_service.get_term_for_date(db, "TEST", date(2026, 2, 14))
    assert t is not None and t.number == 1

    # Inside Term 2, right on the end date (inclusive)
    t = await calendar_service.get_term_for_date(db, "TEST", date(2026, 8, 7))
    assert t is not None and t.number == 2

    # Mid-April holiday — no term covers it
    t = await calendar_service.get_term_for_date(db, "TEST", date(2026, 4, 20))
    assert t is None

    # Year with no calendar at all
    t = await calendar_service.get_term_for_date(db, "TEST", date(2099, 1, 1))
    assert t is None


@pytest.mark.asyncio
async def test_overlap_rejected(db: AsyncSession, _wipe_calendars):
    """Two terms that overlap raise ValueError — would break term billing."""
    with pytest.raises(ValueError, match="overlap"):
        await calendar_service.save_calendar(
            db, "TEST", 2026,
            terms=[
                {"number": 1, "name": "Term 1", "start": "2026-01-13", "end": "2026-05-10"},
                {"number": 2, "name": "Term 2", "start": "2026-05-05", "end": "2026-08-07"},
            ],
            holidays=[],
        )


@pytest.mark.asyncio
async def test_empty_terms_rejected(db: AsyncSession, _wipe_calendars):
    """A calendar with no parseable terms raises — calendar must be
    useful for billing + reports."""
    with pytest.raises(ValueError):
        await calendar_service.save_calendar(
            db, "TEST", 2026, terms=[], holidays=[],
        )


@pytest.mark.asyncio
async def test_copy_shifts_dates_forward(db: AsyncSession, _wipe_calendars):
    """Copy 2026 → 2027 shifts every date by exactly one year."""
    await calendar_service.save_calendar(
        db, "TEST", 2026,
        terms=[
            {"number": 1, "name": "Term 1", "start": "2026-01-13", "end": "2026-04-10"},
        ],
        holidays=[{"name": "Workers' Day", "date": "2026-05-01", "type": "PUBLIC"}],
    )
    await db.commit()

    copied = await calendar_service.copy_calendar(
        db, "TEST", from_year=2026, to_year=2027, updated_by="tester",
    )
    await db.commit()

    assert copied.year == 2027
    assert copied.terms[0].start == date(2027, 1, 13)
    assert copied.terms[0].end == date(2027, 4, 10)
    assert copied.holidays[0].start == date(2027, 5, 1)
    assert "Copied from 2026" in copied.source


@pytest.mark.asyncio
async def test_malformed_row_returns_none(db: AsyncSession, _wipe_calendars):
    """A row with unparseable dates must not crash the resolver — it
    returns None so a bad admin edit can't 500 the report engine."""
    row = SystemSettings(
        key=f"{calendar_service.CALENDAR_KEY_PREFIX}TEST:2026",
        value={"terms": [{"number": 1, "name": "T1", "start": "not-a-date", "end": "nope"}]},
    )
    db.add(row)
    await db.commit()

    cal = await calendar_service.get_calendar(db, "TEST", 2026)
    assert cal is None  # bad dates → service skips them → no terms → None


@pytest.mark.asyncio
async def test_delete_removes_row(db: AsyncSession, _wipe_calendars):
    await calendar_service.save_calendar(
        db, "TEST", 2026,
        terms=[{"number": 1, "name": "T1", "start": "2026-01-01", "end": "2026-03-31"}],
        holidays=[],
    )
    await db.commit()

    removed = await calendar_service.delete_calendar(db, "TEST", 2026)
    assert removed is True
    await db.commit()

    cal = await calendar_service.get_calendar(db, "TEST", 2026)
    assert cal is None

    # Second delete is a no-op
    removed_again = await calendar_service.delete_calendar(db, "TEST", 2026)
    assert removed_again is False


@pytest.mark.asyncio
async def test_missing_upcoming_year_nudge(db: AsyncSession, _wipe_calendars):
    """When we're inside the nudge window and the next year is missing,
    the country appears in the nudge list."""
    # Seed 2026 but not 2027.
    await calendar_service.save_calendar(
        db, "TEST", 2026,
        terms=[{"number": 1, "name": "T1", "start": "2026-01-01", "end": "2026-03-31"}],
        holidays=[],
    )
    await db.commit()

    # Inside the window (90 days before year-end on Dec 31 means today
    # must be Oct 3 or later). Use a date inside the window.
    inside = await calendar_service.countries_missing_upcoming_year(
        db, active_country_codes={"TEST"}, today=date(2026, 11, 1), horizon_days=90,
    )
    assert "TEST" in inside

    # Earlier in the year — outside the window, nudge is quiet.
    outside = await calendar_service.countries_missing_upcoming_year(
        db, active_country_codes={"TEST"}, today=date(2026, 6, 1), horizon_days=90,
    )
    assert outside == []


@pytest.mark.asyncio
async def test_seed_zimbabwe_is_idempotent(db: AsyncSession):
    """Calling the seeder twice doesn't duplicate the row."""
    # Only seed if 2026 is not already set up by another test.
    # Skip wipe — this test verifies platform-wide seed behavior.
    first = await calendar_service.seed_zimbabwe_2026_if_missing(db)
    await db.commit()
    second = await calendar_service.seed_zimbabwe_2026_if_missing(db)
    # second call must not create — the seed row is already there.
    assert second is False, "seed must be idempotent"

    cal = await calendar_service.get_calendar(db, "ZW", 2026)
    assert cal is not None
    assert len(cal.terms) == 3
    # Verify a known Zimbabwean holiday landed.
    assert any(h.name == "Independence Day" for h in cal.holidays)
    # Sanity: Term 1 contains a date in late January.
    t = cal.term_for(date(2026, 1, 20))
    assert t is not None and t.number == 1
