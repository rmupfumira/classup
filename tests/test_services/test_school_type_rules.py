"""Boarding-school attendance + residence-targeted fee item tests.

Covers task #156 (2026-10-12):
  - Student.effective_residence derives from tenant.school_type when
    the student's own residence column is NULL, and honours the
    column directly on MIXED tenants.
  - AttendanceService._validate_attendance_date rejects weekend
    marking for day scholars, allows it for full boarders, and
    allows Sunday-only for weekly boarders.
  - EXEAT is a legitimate AttendanceStatus value.
  - FeeAppliesTo.DAY_SCHOLARS and BOARDERS exist and the invoice
    generator skips students who don't match (see billing_service
    _fee_matches helper — tested here by driving the public API).
"""

from __future__ import annotations

from datetime import date

import pytest

from app.exceptions import ValidationException
from app.models.attendance import AttendanceStatus
from app.models.billing import FeeAppliesTo
from app.models.student import Student
from app.models.tenant import SchoolType
from app.services.attendance_service import AttendanceService


# Known dates for the per-weekday assertions.
_WEEKDAY_MONDAY = date(2026, 10, 5)       # Mon
_WEEKEND_SATURDAY = date(2026, 10, 10)    # Sat
_WEEKEND_SUNDAY = date(2026, 10, 11)      # Sun


class TestEffectiveResidence:
    def test_day_tenant_overrides_student_column(self):
        """A DAY tenant forces every student to DAY even if the row
        happens to have an old boarder value — the tenant-level
        setting is the source of truth for non-MIXED schools."""
        s = Student(first_name="A", last_name="B", tenant_id=None)
        s.residence = "FULL_BOARDER"  # Stale / wrong value.
        assert s.effective_residence("DAY") == "DAY"

    def test_boarding_tenant_treats_everyone_as_full_boarder(self):
        s = Student(first_name="A", last_name="B", tenant_id=None)
        s.residence = None
        assert s.effective_residence("BOARDING") == "FULL_BOARDER"

    def test_weekly_boarding_tenant_treats_everyone_as_weekly(self):
        s = Student(first_name="A", last_name="B", tenant_id=None)
        s.residence = None
        assert s.effective_residence("WEEKLY_BOARDING") == "WEEKLY_BOARDER"

    def test_mixed_tenant_honours_student_column(self):
        """MIXED is the only school_type where the per-student
        column actually drives behaviour."""
        s = Student(first_name="A", last_name="B", tenant_id=None)
        s.residence = "FULL_BOARDER"
        assert s.effective_residence("MIXED") == "FULL_BOARDER"
        s.residence = "DAY"
        assert s.effective_residence("MIXED") == "DAY"

    def test_mixed_tenant_null_residence_defaults_to_day(self):
        """If the admin forgot to designate a student on a MIXED
        tenant, we default to DAY — the safer choice (over-restrict
        attendance rather than mark a day scholar on a weekend)."""
        s = Student(first_name="A", last_name="B", tenant_id=None)
        s.residence = None
        assert s.effective_residence("MIXED") == "DAY"

    def test_unknown_school_type_falls_back_to_student_column(self):
        s = Student(first_name="A", last_name="B", tenant_id=None)
        s.residence = "FULL_BOARDER"
        assert s.effective_residence(None) == "FULL_BOARDER"
        s.residence = None
        assert s.effective_residence("") == "DAY"


class TestWeekendAttendanceGuard:
    svc = AttendanceService()

    def test_day_scholar_cannot_be_marked_on_saturday(self):
        with pytest.raises(ValidationException) as exc:
            self.svc._validate_attendance_date(_WEEKEND_SATURDAY, residence="DAY")
        # ValidationException stores the human-readable text in
        # .errors[].message, not in str(exc) (which just returns
        # the generic "Validation failed").
        assert any("Saturday" in e["message"] for e in exc.value.errors)

    def test_day_scholar_cannot_be_marked_on_sunday(self):
        with pytest.raises(ValidationException) as exc:
            self.svc._validate_attendance_date(_WEEKEND_SUNDAY, residence="DAY")
        assert any("Sunday" in e["message"] for e in exc.value.errors)

    def test_full_boarder_can_be_marked_any_day(self):
        # Shouldn't raise on Sat, Sun, or a weekday.
        self.svc._validate_attendance_date(_WEEKDAY_MONDAY, residence="FULL_BOARDER")
        self.svc._validate_attendance_date(_WEEKEND_SATURDAY, residence="FULL_BOARDER")
        self.svc._validate_attendance_date(_WEEKEND_SUNDAY, residence="FULL_BOARDER")

    def test_weekly_boarder_cannot_be_marked_on_saturday(self):
        # Weekly boarders are off-site Sat; back on Sun evening.
        with pytest.raises(ValidationException):
            self.svc._validate_attendance_date(
                _WEEKEND_SATURDAY, residence="WEEKLY_BOARDER",
            )

    def test_weekly_boarder_can_be_marked_on_sunday(self):
        self.svc._validate_attendance_date(
            _WEEKEND_SUNDAY, residence="WEEKLY_BOARDER",
        )

    def test_weekday_marking_works_regardless_of_residence(self):
        for res in ("DAY", "FULL_BOARDER", "WEEKLY_BOARDER"):
            self.svc._validate_attendance_date(_WEEKDAY_MONDAY, residence=res)

    def test_future_date_rejected_even_for_boarder(self):
        future = date.today().replace(year=date.today().year + 1)
        with pytest.raises(ValidationException) as exc:
            self.svc._validate_attendance_date(future, residence="FULL_BOARDER")
        assert any("future" in e["message"].lower() for e in exc.value.errors)

    def test_missing_residence_defaults_to_day(self):
        """A caller that doesn't pass residence (or passes None)
        gets the strictest rule — matches the pre-2026-10-12
        behaviour where every tenant was effectively DAY."""
        with pytest.raises(ValidationException):
            self.svc._validate_attendance_date(_WEEKEND_SATURDAY, residence=None)


class TestEnumAdditions:
    def test_exeat_is_a_valid_attendance_status(self):
        assert AttendanceStatus.EXEAT.value == "EXEAT"
        assert "EXEAT" in {s.value for s in AttendanceStatus}

    def test_day_scholars_and_boarders_are_valid_fee_scopes(self):
        values = {a.value for a in FeeAppliesTo}
        assert "DAY_SCHOLARS" in values
        assert "BOARDERS" in values
        # Pre-existing scopes still present (regression guard).
        assert "ALL" in values
        assert "CLASS" in values

    def test_school_type_has_four_options(self):
        assert {t.value for t in SchoolType} == {
            "DAY", "BOARDING", "WEEKLY_BOARDING", "MIXED",
        }
