"""Country-aware tenant defaults — grade levels, terminology, terms, grading.

Covers the 2026-10-10 correction pass:
  - Zimbabwe tenants seed ECD A/B + Grade 1-7 + Form 1-6 (not the
    generic Infant/Toddler + Grade 1-12 ladder).
  - South African tenants seed Grade RRR + RR + R + Grade 1-12.
  - Zimbabwe terminology defaults to "Headmaster/Headmistress" and
    "student" (not "learner"), 3 terms/year.
  - South African terminology defaults to "Principal" and "learner",
    4 terms/year.
  - ZIMSEC grading bands are the 2019+ scheme (A/B/C/D/E/U, pass
    mark C at 50-59%). The pre-correction code used A-F-U which was
    Cambridge-shaped and wrong.
  - ZIMSEC Grade 7 Unit scale (1-9) and A-Level points (A=5…E=1)
    exist as separate grading systems.
"""

from __future__ import annotations

import pytest

from app.models.tenant import EducationType, get_default_tenant_settings
from app.services.grade_level_service import _grade_level_catalogue
from app.utils import curriculum_packs as cp


class TestZimbabweDefaults:
    def test_daycare_uses_ecd_a_b(self):
        catalogue = _grade_level_catalogue(EducationType.DAYCARE, "ZW")
        codes = [g["code"] for g in catalogue]
        assert codes == ["ECD_A", "ECD_B"]

    def test_high_school_uses_forms_including_lower_upper_six(self):
        catalogue = _grade_level_catalogue(EducationType.HIGH_SCHOOL, "ZW")
        codes = [g["code"] for g in catalogue]
        assert codes == [
            "FORM_1", "FORM_2", "FORM_3", "FORM_4", "LOWER_SIX", "UPPER_SIX",
        ]

    def test_k12_full_ladder_covers_ecd_to_upper_six(self):
        catalogue = _grade_level_catalogue(EducationType.K12, "ZW")
        codes = [g["code"] for g in catalogue]
        # Must contain the whole ladder, in order, no gaps.
        assert codes[0] == "ECD_A"
        assert codes[-1] == "UPPER_SIX"
        assert "GRADE_7" in codes
        assert "FORM_4" in codes

    def test_terminology_uses_headmaster_and_student(self):
        settings = get_default_tenant_settings(
            EducationType.HIGH_SCHOOL, country_code="ZW",
        )
        terms = settings["terminology"]
        assert terms["principal"] == "Headmaster/Headmistress"
        # ZW uses "student" not "learner" — explicit override.
        assert terms["student"] == "student"
        assert terms["students"] == "students"

    def test_terms_per_year_is_three(self):
        settings = get_default_tenant_settings(
            EducationType.PRIMARY_SCHOOL, country_code="ZW",
        )
        assert settings["terms_per_year"] == 3


class TestSouthAfricaDefaults:
    def test_daycare_uses_grade_rrr_rr_r(self):
        catalogue = _grade_level_catalogue(EducationType.DAYCARE, "ZA")
        codes = [g["code"] for g in catalogue]
        assert codes == ["GRADE_RRR", "GRADE_RR", "GRADE_R"]

    def test_primary_school_includes_ecd_plus_grade_1_through_7(self):
        catalogue = _grade_level_catalogue(EducationType.PRIMARY_SCHOOL, "ZA")
        codes = [g["code"] for g in catalogue]
        # Pre-primary RRR/RR/R + Foundation + Intermediate + Senior start.
        assert codes[:3] == ["GRADE_RRR", "GRADE_RR", "GRADE_R"]
        assert codes[-1] == "GRADE_7"

    def test_high_school_covers_grade_8_through_12(self):
        catalogue = _grade_level_catalogue(EducationType.HIGH_SCHOOL, "ZA")
        codes = [g["code"] for g in catalogue]
        assert codes == ["GRADE_8", "GRADE_9", "GRADE_10", "GRADE_11", "GRADE_12"]

    def test_terminology_uses_principal_and_learner(self):
        settings = get_default_tenant_settings(
            EducationType.HIGH_SCHOOL, country_code="ZA",
        )
        terms = settings["terminology"]
        assert terms["principal"] == "Principal"
        assert terms["student"] == "learner"

    def test_terms_per_year_is_four(self):
        settings = get_default_tenant_settings(
            EducationType.PRIMARY_SCHOOL, country_code="ZA",
        )
        assert settings["terms_per_year"] == 4


class TestGenericFallbackUnchanged:
    """Unknown country code → generic Infant/Toddler + Grade 1-12 (the
    pre-correction behaviour, kept so tenants in unsupported countries
    still see something sensible)."""

    def test_unknown_country_uses_generic_daycare(self):
        catalogue = _grade_level_catalogue(EducationType.DAYCARE, "FR")
        codes = [g["code"] for g in catalogue]
        assert codes == ["INFANT", "TODDLER", "PRESCHOOL", "KINDERGARTEN"]

    def test_no_country_at_all_uses_generic(self):
        catalogue = _grade_level_catalogue(EducationType.HIGH_SCHOOL, None)
        codes = [g["code"] for g in catalogue]
        assert codes == ["GRADE_8", "GRADE_9", "GRADE_10", "GRADE_11", "GRADE_12"]


class TestZimsecGradingCorrections:
    def test_o_level_pass_mark_is_c_at_fifty_percent(self):
        bands = cp._ZIMSEC_O_LEVEL_GRADING.bands
        c = next(b for b in bands if b.grade == "C")
        assert c.min == 50 and c.max == 59

    def test_o_level_scale_has_six_grades_a_to_u(self):
        bands = cp._ZIMSEC_O_LEVEL_GRADING.bands
        assert [b.grade for b in bands] == ["A", "B", "C", "D", "E", "U"]
        # Was A/B/C/D/E/F/U pre-correction — F must not exist.
        assert "F" not in {b.grade for b in bands}

    def test_grade_7_unit_scale_one_is_best(self):
        bands = cp._ZIMSEC_GRADE_7_GRADING.bands
        best = bands[0]
        worst = bands[-1]
        assert best.grade == "1"
        assert worst.grade == "9"
        # Grade 7 Units: lower aggregate is better, so points mirror the
        # unit value (1 point for a 1, 9 points for a 9).
        assert best.points == 1.0
        assert worst.points == 9.0

    def test_a_level_points_descending_a_to_e(self):
        bands = cp._ZIMSEC_A_LEVEL_GRADING.bands
        points = {b.grade: b.points for b in bands}
        assert points["A"] == 5.0
        assert points["B"] == 4.0
        assert points["C"] == 3.0
        assert points["D"] == 2.0
        assert points["E"] == 1.0
        assert points["U"] == 0.0

    def test_nsc_7_level_unchanged(self):
        # The ZA NSC scale was already correct — guard against accidental
        # regression during the correction pass.
        bands = cp._NSC_GRADING.bands
        seven = next(b for b in bands if b.grade == "7")
        one = next(b for b in bands if b.grade == "1")
        assert seven.min == 80 and seven.max == 100
        assert one.min == 0 and one.max == 29
