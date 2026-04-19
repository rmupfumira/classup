"""Tests for the timetable service: CRUD, smart draft generator, views.

Covers happy path, edge cases, and error conditions.
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ForbiddenException, NotFoundException, ValidationException
from app.models import (
    ClassSubject,
    SchoolClass,
    Student,
    Subject,
    TeacherClass,
    Tenant,
    Timetable,
    TimetableEntry,
    User,
)
from app.models.user import Role
from app.services.timetable_service import get_timetable_service
from app.utils.security import hash_password
from app.utils.tenant_context import _current_user_id, _current_user_role, _tenant_id


# =========================================================================
# Feature flag guard
# =========================================================================


class TestFeatureFlagGuard:
    async def test_feature_enabled_passes(self, db: AsyncSession, test_tenant: Tenant):
        """Enabled tenant should pass the guard."""
        svc = get_timetable_service()
        # Should not raise
        await svc._require_feature_enabled(db)

    async def test_feature_disabled_raises(self, db: AsyncSession, test_tenant: Tenant):
        """Disabled tenant should raise ForbiddenException."""
        # Disable
        settings = dict(test_tenant.settings or {})
        features = dict(settings.get("features", {}))
        features["timetable_management"] = False
        settings["features"] = features
        test_tenant.settings = settings
        await db.commit()

        svc = get_timetable_service()
        with pytest.raises(ForbiddenException, match="not enabled"):
            await svc._require_feature_enabled(db)


# =========================================================================
# Config
# =========================================================================


class TestConfig:
    async def test_get_or_create_creates_default(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        """First access should create default config with 10 periods."""
        svc = get_timetable_service()
        config = await svc.get_or_create_config(db)

        assert config.id is not None
        assert config.tenant_id == test_tenant.id
        assert config.days == ["MON", "TUE", "WED", "THU", "FRI"]
        assert len(config.periods) == 10
        assert any(p["is_break"] for p in config.periods)
        assert any(not p["is_break"] for p in config.periods)

    async def test_get_or_create_returns_existing(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        """Subsequent calls should return the same config."""
        svc = get_timetable_service()
        c1 = await svc.get_or_create_config(db)
        await db.commit()
        c2 = await svc.get_or_create_config(db)
        assert c1.id == c2.id

    async def test_update_config_replaces_days_and_periods(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        """update_config should replace days and periods."""
        svc = get_timetable_service()
        new_days = ["MON", "TUE", "WED"]
        new_periods = [
            {"index": 1, "label": "Morning", "start_time": "09:00", "end_time": "10:00", "is_break": False},
            {"index": 2, "label": "Break", "start_time": "10:00", "end_time": "10:15", "is_break": True},
            {"index": 3, "label": "Afternoon", "start_time": "10:15", "end_time": "11:15", "is_break": False},
        ]
        config = await svc.update_config(db, new_days, new_periods)

        assert config.days == new_days
        assert len(config.periods) == 3
        assert config.periods[0]["label"] == "Morning"


# =========================================================================
# Timetable CRUD
# =========================================================================


class TestTimetableCrud:
    async def test_create_timetable_happy_path(
        self, db: AsyncSession, test_class_with_subjects: dict
    ):
        """Create a timetable for a valid class."""
        cls = test_class_with_subjects["class"]
        svc = get_timetable_service()
        tt = await svc.create_timetable(db, cls.id, "Grade 5A - Term 1")

        assert tt.id is not None
        assert tt.class_id == cls.id
        assert tt.name == "Grade 5A - Term 1"
        assert tt.is_active is True

    async def test_create_timetable_deactivates_previous_active(
        self, db: AsyncSession, test_class_with_subjects: dict
    ):
        """Creating a new timetable for a class deactivates the old active one."""
        cls = test_class_with_subjects["class"]
        svc = get_timetable_service()

        tt1 = await svc.create_timetable(db, cls.id, "Term 1")
        await db.commit()
        tt2 = await svc.create_timetable(db, cls.id, "Term 2")
        await db.commit()

        # Refresh tt1
        await db.refresh(tt1)
        assert tt1.is_active is False
        assert tt2.is_active is True

    async def test_create_timetable_unknown_class_raises(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        """Creating for a non-existent class raises NotFoundException."""
        svc = get_timetable_service()
        fake_id = uuid.uuid4()
        with pytest.raises(NotFoundException):
            await svc.create_timetable(db, fake_id, "Phantom Timetable")

    async def test_get_timetable_returns_with_entries(
        self, db: AsyncSession, test_class_with_subjects: dict
    ):
        """get_timetable should return with entries relationship loaded."""
        cls = test_class_with_subjects["class"]
        svc = get_timetable_service()
        tt = await svc.create_timetable(db, cls.id, "Test")
        await db.commit()

        fetched = await svc.get_timetable(db, tt.id)
        assert fetched.id == tt.id
        assert fetched.entries == []
        assert fetched.school_class is not None

    async def test_get_timetable_not_found_raises(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        """get_timetable with bad id raises NotFoundException."""
        svc = get_timetable_service()
        with pytest.raises(NotFoundException):
            await svc.get_timetable(db, uuid.uuid4())

    async def test_list_timetables_pagination(
        self, db: AsyncSession, test_class_with_subjects: dict
    ):
        """list_timetables returns paginated results."""
        cls = test_class_with_subjects["class"]
        svc = get_timetable_service()
        await svc.create_timetable(db, cls.id, "Only One")
        await db.commit()

        items, total = await svc.list_timetables(db, page=1, page_size=10)
        assert total >= 1
        assert any(t.name == "Only One" for t in items)

    async def test_delete_timetable_soft_deletes(
        self, db: AsyncSession, test_class_with_subjects: dict
    ):
        """Delete should soft-delete and deactivate."""
        cls = test_class_with_subjects["class"]
        svc = get_timetable_service()
        tt = await svc.create_timetable(db, cls.id, "Doomed")
        await db.commit()
        await svc.delete_timetable(db, tt.id)
        await db.commit()

        # Fetching now raises (soft-deleted records are filtered)
        with pytest.raises(NotFoundException):
            await svc.get_timetable(db, tt.id)


# =========================================================================
# Tenant isolation
# =========================================================================


class TestTenantIsolation:
    async def test_cannot_access_other_tenants_timetable(
        self, db: AsyncSession, test_class_with_subjects: dict
    ):
        """Switching tenant context should hide the timetable."""
        cls = test_class_with_subjects["class"]
        svc = get_timetable_service()
        tt = await svc.create_timetable(db, cls.id, "Isolated")
        await db.commit()

        # Switch to a different tenant id
        other = uuid.uuid4()
        token = _tenant_id.set(other)
        try:
            with pytest.raises(NotFoundException):
                await svc.get_timetable(db, tt.id)
        finally:
            _tenant_id.reset(token)


# =========================================================================
# Entry CRUD
# =========================================================================


class TestEntryCrud:
    async def test_set_entry_creates_cell(
        self, db: AsyncSession, test_class_with_subjects: dict
    ):
        cls = test_class_with_subjects["class"]
        subj = test_class_with_subjects["subjects"][0]
        teacher = test_class_with_subjects["teacher"]
        svc = get_timetable_service()
        tt = await svc.create_timetable(db, cls.id, "Test")
        await db.commit()

        entry = await svc.set_entry(
            db, tt.id, "MON", 1, subj.id, teacher.id
        )
        assert entry.id is not None
        assert entry.day == "MON"
        assert entry.period_index == 1
        assert entry.subject_id == subj.id
        assert entry.teacher_id == teacher.id

    async def test_set_entry_upserts_existing(
        self, db: AsyncSession, test_class_with_subjects: dict
    ):
        """Calling set_entry twice on the same slot should update, not duplicate."""
        cls = test_class_with_subjects["class"]
        subjs = test_class_with_subjects["subjects"]
        svc = get_timetable_service()
        tt = await svc.create_timetable(db, cls.id, "Test")
        await db.commit()

        e1 = await svc.set_entry(db, tt.id, "MON", 1, subjs[0].id, None)
        await db.commit()
        e2 = await svc.set_entry(db, tt.id, "MON", 1, subjs[1].id, None)
        await db.commit()

        assert e1.id == e2.id
        assert e2.subject_id == subjs[1].id

    async def test_set_entry_with_foreign_subject_raises(
        self,
        db: AsyncSession,
        test_tenant: Tenant,
        test_class_with_subjects: dict,
    ):
        """Assigning a subject not in class_subjects raises ValidationException."""
        cls = test_class_with_subjects["class"]
        svc = get_timetable_service()
        tt = await svc.create_timetable(db, cls.id, "Test")
        await db.commit()

        # Create a subject that isn't assigned to the class
        foreign = Subject(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            name="Foreign",
            code="FOR",
            is_active=True,
        )
        db.add(foreign)
        await db.commit()

        with pytest.raises(ValidationException):
            await svc.set_entry(db, tt.id, "MON", 1, foreign.id, None)

    async def test_clear_entry_deletes(
        self, db: AsyncSession, test_class_with_subjects: dict
    ):
        cls = test_class_with_subjects["class"]
        subj = test_class_with_subjects["subjects"][0]
        svc = get_timetable_service()
        tt = await svc.create_timetable(db, cls.id, "Test")
        await db.commit()

        await svc.set_entry(db, tt.id, "MON", 1, subj.id, None)
        await db.commit()
        deleted = await svc.clear_entry(db, tt.id, "MON", 1)
        assert deleted is True

        # Clearing again returns False
        deleted_again = await svc.clear_entry(db, tt.id, "MON", 1)
        assert deleted_again is False


# =========================================================================
# Smart draft generator
# =========================================================================


class TestGenerateDraft:
    async def test_generate_fills_all_slots(
        self, db: AsyncSession, test_class_with_subjects: dict
    ):
        """Default generate should fill all teaching (non-break) slots."""
        cls = test_class_with_subjects["class"]
        svc = get_timetable_service()
        tt = await svc.create_timetable(db, cls.id, "Auto")
        await db.commit()

        created, warnings = await svc.generate_draft(db, tt.id)
        await db.commit()

        # Default config: 5 days × 8 non-break periods = 40 slots
        assert created == 40
        assert warnings == []

    async def test_generate_assigns_primary_teacher(
        self, db: AsyncSession, test_class_with_subjects: dict
    ):
        """All generated entries should use the primary teacher."""
        cls = test_class_with_subjects["class"]
        teacher = test_class_with_subjects["teacher"]
        svc = get_timetable_service()
        tt = await svc.create_timetable(db, cls.id, "Auto")
        await db.commit()

        await svc.generate_draft(db, tt.id)
        await db.commit()

        tt2 = await svc.get_timetable(db, tt.id)
        assert all(e.teacher_id == teacher.id for e in tt2.entries)

    async def test_generate_uses_all_subjects(
        self, db: AsyncSession, test_class_with_subjects: dict
    ):
        """Every assigned subject should appear in the generated grid."""
        cls = test_class_with_subjects["class"]
        subjects = test_class_with_subjects["subjects"]
        svc = get_timetable_service()
        tt = await svc.create_timetable(db, cls.id, "Auto")
        await db.commit()

        await svc.generate_draft(db, tt.id)
        await db.commit()

        tt2 = await svc.get_timetable(db, tt.id)
        used_subject_ids = {e.subject_id for e in tt2.entries}
        for s in subjects:
            assert s.id in used_subject_ids

    async def test_generate_skips_breaks(
        self, db: AsyncSession, test_class_with_subjects: dict
    ):
        """Generated entries must not land on break periods."""
        cls = test_class_with_subjects["class"]
        svc = get_timetable_service()
        tt = await svc.create_timetable(db, cls.id, "Auto")
        await db.commit()
        await svc.generate_draft(db, tt.id)
        await db.commit()

        config = await svc.get_or_create_config(db)
        break_indices = {p["index"] for p in config.periods if p["is_break"]}

        tt2 = await svc.get_timetable(db, tt.id)
        for e in tt2.entries:
            assert e.period_index not in break_indices

    async def test_generate_spreads_subjects_across_days(
        self, db: AsyncSession, test_class_with_subjects: dict
    ):
        """Each subject should appear on at least 3 different days (spreading)."""
        cls = test_class_with_subjects["class"]
        subjects = test_class_with_subjects["subjects"]
        svc = get_timetable_service()
        tt = await svc.create_timetable(db, cls.id, "Auto")
        await db.commit()
        await svc.generate_draft(db, tt.id)
        await db.commit()

        tt2 = await svc.get_timetable(db, tt.id)
        days_by_subject: dict[uuid.UUID, set[str]] = {}
        for e in tt2.entries:
            days_by_subject.setdefault(e.subject_id, set()).add(e.day)
        for s in subjects:
            assert len(days_by_subject.get(s.id, set())) >= 3, \
                f"Subject {s.name} only on days {days_by_subject.get(s.id)}"

    async def test_generate_respects_weekly_hours(
        self, db: AsyncSession, test_class_with_subjects: dict
    ):
        """weekly_hours override should produce exactly the requested slot counts."""
        cls = test_class_with_subjects["class"]
        subjects = test_class_with_subjects["subjects"]
        svc = get_timetable_service()
        tt = await svc.create_timetable(db, cls.id, "Auto")
        await db.commit()

        # Request specific counts for 2 subjects; the third gets the remainder
        weekly_hours = {str(subjects[0].id): 10, str(subjects[1].id): 15}
        await svc.generate_draft(db, tt.id, weekly_hours=weekly_hours)
        await db.commit()

        tt2 = await svc.get_timetable(db, tt.id)
        count_by_subject: dict[uuid.UUID, int] = {}
        for e in tt2.entries:
            count_by_subject[e.subject_id] = count_by_subject.get(e.subject_id, 0) + 1
        assert count_by_subject.get(subjects[0].id) == 10
        assert count_by_subject.get(subjects[1].id) == 15
        assert count_by_subject.get(subjects[2].id) == 15  # 40 - 10 - 15

    async def test_generate_clears_existing_entries(
        self, db: AsyncSession, test_class_with_subjects: dict
    ):
        """Re-running generate should replace existing entries, not stack."""
        cls = test_class_with_subjects["class"]
        svc = get_timetable_service()
        tt = await svc.create_timetable(db, cls.id, "Auto")
        await db.commit()

        await svc.generate_draft(db, tt.id)
        await db.commit()
        first_count = len((await svc.get_timetable(db, tt.id)).entries)

        await svc.generate_draft(db, tt.id)
        await db.commit()
        second_count = len((await svc.get_timetable(db, tt.id)).entries)
        assert first_count == second_count == 40

    async def test_generate_without_subjects_raises(
        self,
        db: AsyncSession,
        test_tenant: Tenant,
        test_class: SchoolClass,
    ):
        """Generating for a class with no subjects assigned raises ValidationException."""
        svc = get_timetable_service()
        tt = await svc.create_timetable(db, test_class.id, "Empty")
        await db.commit()
        with pytest.raises(ValidationException) as exc_info:
            await svc.generate_draft(db, tt.id)
        # Check the error mentions subjects
        assert any("subject" in str(e).lower() for e in (exc_info.value.errors or []))

    async def test_generate_without_teacher_warns(
        self,
        db: AsyncSession,
        test_tenant: Tenant,
        test_class: SchoolClass,
    ):
        """Class with subjects but no teacher_class produces a warning."""
        svc = get_timetable_service()
        # Add a subject
        s = Subject(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            name="Solo",
            code="SOLO",
            is_active=True,
        )
        db.add(s)
        await db.commit()
        cs = ClassSubject(
            id=uuid.uuid4(),
            class_id=test_class.id,
            subject_id=s.id,
            is_compulsory=True,
            display_order=0,
        )
        db.add(cs)
        await db.commit()

        tt = await svc.create_timetable(db, test_class.id, "NoTeacher")
        await db.commit()
        created, warnings = await svc.generate_draft(db, tt.id)
        await db.commit()
        assert created == 40
        assert any("No teacher" in w for w in warnings)

    async def test_generate_detects_cross_timetable_conflicts(
        self,
        db: AsyncSession,
        test_tenant: Tenant,
        test_class_with_subjects: dict,
    ):
        """When the teacher is already booked in another active timetable,
        the generator should either place around the conflict or warn."""
        svc = get_timetable_service()
        teacher = test_class_with_subjects["teacher"]

        # Create a second class sharing the same teacher
        cls2 = SchoolClass(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            name="Grade 5B",
            is_active=True,
        )
        db.add(cls2)
        await db.commit()

        subj2 = Subject(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            name="Math2",
            code="MAT2",
            is_active=True,
        )
        db.add(subj2)
        await db.commit()

        cs2 = ClassSubject(
            id=uuid.uuid4(),
            class_id=cls2.id,
            subject_id=subj2.id,
            is_compulsory=True,
            display_order=0,
        )
        tc2 = TeacherClass(
            id=uuid.uuid4(),
            teacher_id=teacher.id,
            class_id=cls2.id,
            is_primary=True,
        )
        db.add_all([cs2, tc2])
        await db.commit()

        # Generate for first class — takes all 40 slots for that teacher
        tt1 = await svc.create_timetable(
            db, test_class_with_subjects["class"].id, "C1"
        )
        await db.commit()
        await svc.generate_draft(db, tt1.id)
        await db.commit()

        # Generate for second class — the teacher is now fully booked
        tt2 = await svc.create_timetable(db, cls2.id, "C2")
        await db.commit()
        created, warnings = await svc.generate_draft(db, tt2.id)
        await db.commit()

        # Should fail to place any slots (all blocked) OR place 0 with warnings
        assert created == 0 or len(warnings) > 0


# =========================================================================
# Detect conflicts
# =========================================================================


class TestDetectConflicts:
    async def test_no_conflicts_returns_empty(
        self, db: AsyncSession, test_class_with_subjects: dict
    ):
        cls = test_class_with_subjects["class"]
        svc = get_timetable_service()
        tt = await svc.create_timetable(db, cls.id, "Clean")
        await db.commit()
        await svc.generate_draft(db, tt.id)
        await db.commit()

        conflicts = await svc.detect_conflicts(db, tt.id)
        assert conflicts == set()

    async def test_teacher_in_two_active_timetables_at_same_slot_flags_conflict(
        self,
        db: AsyncSession,
        test_tenant: Tenant,
        test_class_with_subjects: dict,
    ):
        """Manually create overlapping bookings across two active timetables."""
        svc = get_timetable_service()
        teacher = test_class_with_subjects["teacher"]
        subj = test_class_with_subjects["subjects"][0]

        # Another class
        cls2 = SchoolClass(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            name="Grade 5B",
            is_active=True,
        )
        db.add(cls2)
        await db.commit()
        cs2 = ClassSubject(
            id=uuid.uuid4(),
            class_id=cls2.id,
            subject_id=subj.id,
            is_compulsory=True,
            display_order=0,
        )
        db.add(cs2)
        await db.commit()

        tt1 = await svc.create_timetable(
            db, test_class_with_subjects["class"].id, "T1"
        )
        await db.commit()
        tt2 = await svc.create_timetable(db, cls2.id, "T2")
        await db.commit()

        # Both get the same teacher at MON / period 1
        await svc.set_entry(db, tt1.id, "MON", 1, subj.id, teacher.id)
        await svc.set_entry(db, tt2.id, "MON", 1, subj.id, teacher.id)
        await db.commit()

        conflicts = await svc.detect_conflicts(db, tt1.id)
        assert ("MON", 1) in conflicts


# =========================================================================
# Views: teacher / student
# =========================================================================


class TestViews:
    async def test_get_teacher_timetable_returns_their_lessons(
        self, db: AsyncSession, test_class_with_subjects: dict
    ):
        cls = test_class_with_subjects["class"]
        teacher = test_class_with_subjects["teacher"]
        svc = get_timetable_service()
        tt = await svc.create_timetable(db, cls.id, "T")
        await db.commit()
        await svc.generate_draft(db, tt.id)
        await db.commit()

        entries = await svc.get_teacher_timetable(db, teacher.id)
        assert len(entries) == 40
        assert all(e.teacher_id == teacher.id for e in entries)

    async def test_get_teacher_timetable_empty_for_other_user(
        self, db: AsyncSession, test_class_with_subjects: dict
    ):
        """A user with no lessons should get an empty list."""
        svc = get_timetable_service()
        entries = await svc.get_teacher_timetable(db, uuid.uuid4())
        assert entries == []

    async def test_get_student_timetable_returns_class_timetable(
        self,
        db: AsyncSession,
        test_tenant: Tenant,
        test_class_with_subjects: dict,
    ):
        """Student in a class should see that class's active timetable."""
        cls = test_class_with_subjects["class"]
        svc = get_timetable_service()
        tt = await svc.create_timetable(db, cls.id, "Active")
        await db.commit()
        await svc.generate_draft(db, tt.id)
        await db.commit()

        # Create a student
        student = Student(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            first_name="Alice",
            last_name="Test",
            class_id=cls.id,
            is_active=True,
        )
        db.add(student)
        await db.commit()

        result = await svc.get_student_timetable(db, student.id)
        assert result is not None
        assert result.id == tt.id
        assert len(result.entries) == 40

    async def test_get_student_timetable_no_class_returns_none(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        """A student with no class assigned returns None."""
        student = Student(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            first_name="Bob",
            last_name="Floating",
            class_id=None,
            is_active=True,
        )
        db.add(student)
        await db.commit()

        svc = get_timetable_service()
        result = await svc.get_student_timetable(db, student.id)
        assert result is None

    async def test_get_student_timetable_unknown_id_returns_none(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        svc = get_timetable_service()
        result = await svc.get_student_timetable(db, uuid.uuid4())
        assert result is None
