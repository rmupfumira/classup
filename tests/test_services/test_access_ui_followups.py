"""UI follow-ups to the access-control rules (2026-11-21).

- events carry ``can_edit`` (admins always, teachers only their own)
- exam detail knows which papers the viewer may mark; mark_paper 404s otherwise
- MIS pages give teachers only their class-teacher classes, defaulting sensibly
- the daily attendance page shows a friendly state for subject-only teachers
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from starlette.requests import Request

from app.exceptions import NotFoundException
from app.models import SchoolEvent, Subject
from app.models.academic import ClassSubjectTeacher
from app.models.exam import Exam, ExamSubject
from app.services.exam_service import get_exam_service
from tests.test_services.test_access_scope import as_user, world  # noqa: F401


def _request(path: str = "/", cookies: str = "") -> Request:
    headers = [(b"cookie", cookies.encode())] if cookies else []
    return Request({
        "type": "http", "method": "GET", "path": path, "query_string": b"",
        "headers": headers, "scheme": "http", "server": ("testserver", 80),
        "root_path": "", "app": None,
    })


async def _subject_only_world(db, world, tenant):
    """Teacher is class teacher of A and teaches Art (only) in B."""
    art = Subject(id=uuid.uuid4(), tenant_id=tenant.id, name="Art",
                  code=f"ART-{uuid.uuid4().hex[:4]}", is_active=True)
    maths = Subject(id=uuid.uuid4(), tenant_id=tenant.id, name="Maths",
                    code=f"MAT-{uuid.uuid4().hex[:4]}", is_active=True)
    exam = Exam(id=uuid.uuid4(), tenant_id=tenant.id, class_id=world["class_b"].id,
                name="T1", academic_year="2026", term=1)
    db.add_all([art, maths, exam])
    await db.flush()
    art_paper = ExamSubject(id=uuid.uuid4(), tenant_id=tenant.id, exam_id=exam.id,
                            subject_id=art.id, total_marks=100)
    maths_paper = ExamSubject(id=uuid.uuid4(), tenant_id=tenant.id, exam_id=exam.id,
                              subject_id=maths.id, total_marks=100)
    db.add_all([art_paper, maths_paper, ClassSubjectTeacher(
        id=uuid.uuid4(), class_id=world["class_b"].id, subject_id=art.id,
        teacher_id=world["teacher"].id,
    )])
    tenant.settings = {**tenant.settings, "subject_teacher_enabled": True,
                       "features": {**tenant.settings.get("features", {}),
                                    "exam_management": True, "mis": True}}
    await db.commit()
    return exam, art_paper, maths_paper


class TestEventCanEdit:
    async def test_can_edit_flag(self, db, world, test_tenant):
        from app.api.v1.events import _event_to_dict

        def ev(created_by):
            e = SchoolEvent(id=uuid.uuid4(), tenant_id=test_tenant.id, created_by=created_by,
                            title="E", event_type="OTHER", scope="SCHOOL",
                            starts_at=datetime.now(timezone.utc) + timedelta(days=1))
            e.rsvps, e.school_class, e.student = [], None, None
            return e

        mine, theirs = ev(world["teacher"].id), ev(world["admin"].id)
        with as_user(world["teacher"]):
            assert _event_to_dict(mine)["can_edit"] is True
            assert _event_to_dict(theirs)["can_edit"] is False
        with as_user(world["admin"]):
            assert _event_to_dict(mine)["can_edit"] is True
        with as_user(world["parent_a"]):
            assert _event_to_dict(theirs)["can_edit"] is False


class TestExamMarking:
    async def test_markable_papers_and_mark_route(self, db, world, test_tenant):
        from app.web.exams import mark_paper

        exam, art_paper, maths_paper = await _subject_only_world(db, world, test_tenant)
        svc = get_exam_service()
        with as_user(world["teacher"]):
            exam_row = await svc.get_exam(db, exam.id)
            assert await svc.markable_paper_ids(db, exam_row) == {art_paper.id}
            with pytest.raises(NotFoundException):
                await mark_paper(_request(), exam.id, maths_paper.id, db)
        with as_user(world["admin"]):
            exam_row = await svc.get_exam(db, exam.id)
            assert await svc.markable_paper_ids(db, exam_row) == {art_paper.id, maths_paper.id}


class TestMisClassContext:
    async def test_teacher_gets_only_class_teacher_classes(self, db, world, test_tenant):
        from app.web.mis import _mis_class_context

        await _subject_only_world(db, world, test_tenant)
        with as_user(world["teacher"]):
            ctx = await _mis_class_context(_request(), db)
            assert ctx["mis_teacher"] is True
            assert [c.id for c in ctx["mis_classes"]] == [world["class_a"].id]
            assert ctx["mis_default_class_id"] == world["class_a"].id
            # A subject-only class in the cookie doesn't become the default.
            ctx = await _mis_class_context(
                _request(cookies=f"selected_class_id={world['class_b'].id}"), db,
            )
            assert ctx["mis_default_class_id"] == world["class_a"].id
        with as_user(world["admin"]):
            assert (await _mis_class_context(_request(), db))["mis_teacher"] is False


class TestAttendanceSubjectOnly:
    async def test_subject_only_class_gets_friendly_state(self, db, world, test_tenant):
        from app.web import attendance as attendance_web

        await _subject_only_world(db, world, test_tenant)
        captured = {}

        class _Capture:
            def TemplateResponse(self, name, context):  # noqa: N802
                captured["name"], captured["ctx"] = name, context
                return context

        real = attendance_web.templates
        attendance_web.templates = _Capture()
        try:
            with as_user(world["teacher"]):
                await attendance_web.attendance_daily(
                    _request("/attendance"), class_id=world["class_b"].id, target_date=None, db=db,
                )
                assert captured["ctx"]["not_class_teacher"] is True
                assert captured["ctx"]["attendance_data"] is None
                assert [c.id for c in captured["ctx"]["classes"]] == [world["class_a"].id]

                await attendance_web.attendance_daily(
                    _request("/attendance", cookies=f"selected_class_id={world['class_b'].id}"),
                    class_id=None, target_date=None, db=db,
                )
                assert captured["ctx"]["not_class_teacher"] is False
                assert captured["ctx"]["current_class_id"] == world["class_a"].id
        finally:
            attendance_web.templates = real
