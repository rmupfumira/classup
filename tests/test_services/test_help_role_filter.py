"""Help & Guides role-filter — 2026-10-09 redesign.

Topics now stack by role: parent < teacher < school_admin < super_admin.
A parent must not see an admin topic even if they guess the slug; an
admin must still see parent topics so they can answer parent questions.
"""

from __future__ import annotations

import pytest

from app.help_content import (
    HELP_TOPICS,
    get_topics_for_role,
    visible_role_keys,
)


class TestVisibleRoleKeys:
    def test_super_admin_sees_everything(self):
        assert visible_role_keys("SUPER_ADMIN") == {
            "super_admin", "school_admin", "teacher", "parent",
        }

    def test_school_admin_sees_down_to_parent(self):
        """Admins should see teacher + parent topics too — they often
        answer parent questions and need the same answers at hand."""
        assert visible_role_keys("SCHOOL_ADMIN") == {
            "school_admin", "teacher", "parent",
        }

    def test_teacher_sees_teacher_and_parent(self):
        assert visible_role_keys("TEACHER") == {"teacher", "parent"}

    def test_parent_sees_parent_only(self):
        assert visible_role_keys("PARENT") == {"parent"}

    def test_unknown_role_defaults_to_parent(self):
        assert visible_role_keys("something_weird") == {"parent"}
        assert visible_role_keys("") == {"parent"}
        assert visible_role_keys(None) == {"parent"}


class TestGetTopicsForRole:
    def test_parent_sees_only_parent_topics(self):
        topics = get_topics_for_role("PARENT")
        assert topics, "parent must see at least the new parent topics"
        for t in topics:
            assert "parent" in t.get("roles", []), (
                f"non-parent topic {t['slug']} leaked into parent view"
            )

    def test_parent_cannot_see_admin_topics(self):
        """getting-started is a school_admin topic — must not leak."""
        topics = get_topics_for_role("PARENT")
        slugs = {t["slug"] for t in topics}
        assert "getting-started" not in slugs

    def test_school_admin_sees_both_admin_and_parent_topics(self):
        topics = get_topics_for_role("SCHOOL_ADMIN")
        slugs = {t["slug"] for t in topics}
        # One known admin topic + one known parent topic
        assert "getting-started" in slugs
        assert "parent-attendance" in slugs

    def test_parent_topics_cover_the_common_tasks(self):
        """Smoke check: the 7 parent topics we authored today all land."""
        topics = get_topics_for_role("PARENT")
        slugs = {t["slug"] for t in topics}
        expected = {
            "parent-attendance",
            "parent-report-absence",
            "parent-invoices",
            "parent-events-rsvp",
            "parent-whatsapp",
            "parent-profile",
            "parent-messaging",
        }
        assert expected.issubset(slugs), (
            f"missing parent topics: {expected - slugs}"
        )
