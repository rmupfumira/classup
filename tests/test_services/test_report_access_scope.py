"""Report visibility per role (the original /reports leak, 2026-11).

Parents: FINALIZED reports for their own linked children only.
Teachers: reports in their own classes. Admins: the whole tenant.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailyReport, ReportTemplate, SchoolClass, Student, Tenant, User
from app.models.school_class import TeacherClass
from app.models.student import ParentStudent
from app.models.user import Role
from app.exceptions import ValidationException
from app.schemas.report import ReportCreate
from app.services.report_service import get_report_service
from app.utils.security import hash_password
from tests.test_services.test_access_scope import as_user


def _user(tenant_id, role, name):
    return User(
        id=uuid.uuid4(), tenant_id=tenant_id,
        email=f"{name}-{uuid.uuid4().hex[:8]}@test.local",
        password_hash=hash_password("x"), first_name=name, last_name="R",
        role=role, is_active=True, language="en",
    )


@pytest_asyncio.fixture
async def reports(db: AsyncSession, test_tenant: Tenant, test_admin: User, test_class: SchoolClass):
    class_b = SchoolClass(id=uuid.uuid4(), tenant_id=test_tenant.id, name="Other", is_active=True)
    teacher = _user(test_tenant.id, Role.TEACHER.value, "teacher")
    parent_a = _user(test_tenant.id, Role.PARENT.value, "parenta")
    parent_b = _user(test_tenant.id, Role.PARENT.value, "parentb")
    db.add_all([class_b, teacher, parent_a, parent_b])
    await db.flush()
    child_a = Student(id=uuid.uuid4(), tenant_id=test_tenant.id, first_name="Ann",
                      last_name="A", class_id=test_class.id, is_active=True)
    child_b = Student(id=uuid.uuid4(), tenant_id=test_tenant.id, first_name="Ben",
                      last_name="B", class_id=class_b.id, is_active=True)
    template = ReportTemplate(id=uuid.uuid4(), tenant_id=test_tenant.id, name="Daily",
                              report_type="DAILY_ACTIVITY", frequency="DAILY",
                              sections=[], is_active=True)
    db.add_all([child_a, child_b, template])
    await db.flush()
    db.add_all([
        TeacherClass(id=uuid.uuid4(), teacher_id=teacher.id, class_id=test_class.id, is_primary=True),
        ParentStudent(id=uuid.uuid4(), parent_id=parent_a.id, student_id=child_a.id,
                      relationship_type="PARENT", is_primary=True),
        ParentStudent(id=uuid.uuid4(), parent_id=parent_b.id, student_id=child_b.id,
                      relationship_type="PARENT", is_primary=True),
    ])

    other_tenant = Tenant(id=uuid.uuid4(), name="Elsewhere", slug=f"else-{uuid.uuid4().hex[:8]}",
                          email=f"else-{uuid.uuid4().hex[:8]}@test.local", is_active=True)
    db.add(other_tenant)
    await db.flush()
    f_class = SchoolClass(id=uuid.uuid4(), tenant_id=other_tenant.id, name="F", is_active=True)
    f_admin = _user(other_tenant.id, Role.SCHOOL_ADMIN.value, "fadmin")
    db.add_all([f_class, f_admin])
    await db.flush()
    f_student = Student(id=uuid.uuid4(), tenant_id=other_tenant.id, first_name="Fay",
                        last_name="F", class_id=f_class.id, is_active=True)
    f_template = ReportTemplate(id=uuid.uuid4(), tenant_id=other_tenant.id, name="Daily",
                                report_type="DAILY_ACTIVITY", frequency="DAILY",
                                sections=[], is_active=True)
    db.add_all([f_student, f_template])
    await db.flush()

    def rep(tenant_id, student, class_id, tpl, status, day, by):
        return DailyReport(id=uuid.uuid4(), tenant_id=tenant_id, student_id=student.id,
                           class_id=class_id, template_id=tpl.id, report_date=day,
                           report_data={}, status=status, created_by=by.id)

    d1, d2 = date(2026, 3, 2), date(2026, 3, 3)
    rows = {
        "a_final": rep(test_tenant.id, child_a, test_class.id, template, "FINALIZED", d1, test_admin),
        "a_draft": rep(test_tenant.id, child_a, test_class.id, template, "DRAFT", d2, test_admin),
        "b_final": rep(test_tenant.id, child_b, class_b.id, template, "FINALIZED", d1, test_admin),
        "foreign": rep(other_tenant.id, f_student, f_class.id, f_template, "FINALIZED", d1, f_admin),
    }
    db.add_all(rows.values())
    await db.commit()
    other_tenant_id = other_tenant.id
    try:
        yield {"teacher": teacher, "parent_a": parent_a, "parent_b": parent_b,
               "admin": test_admin, "child_a": child_a, "child_b": child_b,
               "class_a": test_class, "class_b": class_b, "template": template,
               "f_student": f_student, "f_class": f_class, "f_template": f_template, **rows}
    finally:
        await db.execute(delete(Tenant).where(Tenant.id == other_tenant_id))
        await db.commit()


def _ids(listing) -> set:
    items, _ = listing
    return {uuid.UUID(str(i["id"] if isinstance(i, dict) else i.id)) for i in items}


class TestReportScope:
    async def test_parent_sees_only_own_childs_finalized_reports(self, db, reports):
        svc = get_report_service()
        with as_user(reports["parent_a"]):
            assert _ids(await svc.get_reports(db)) == {reports["a_final"].id}
            assert _ids(await svc.get_student_reports(db, reports["child_b"].id)) == set()
            assert await svc.get_report(db, reports["a_final"].id) is not None
            assert await svc.get_report(db, reports["a_draft"].id) is None
            assert await svc.get_report(db, reports["b_final"].id) is None
            assert await svc.get_report(db, reports["foreign"].id) is None

    async def test_teacher_sees_only_own_class(self, db, reports):
        svc = get_report_service()
        with as_user(reports["teacher"]):
            assert _ids(await svc.get_reports(db)) == {reports["a_final"].id, reports["a_draft"].id}
            assert await svc.get_report(db, reports["b_final"].id) is None

    async def test_admin_sees_whole_tenant_only(self, db, reports):
        svc = get_report_service()
        with as_user(reports["admin"]):
            assert _ids(await svc.get_reports(db)) == {
                reports["a_final"].id, reports["a_draft"].id, reports["b_final"].id,
            }
            assert await svc.get_report(db, reports["foreign"].id) is None


def _create(student, class_, template):
    return ReportCreate(student_id=student.id, class_id=class_.id, template_id=template.id,
                        report_date=date(2026, 3, 4), report_data={})


class TestReportCreateScope:
    async def test_teacher_can_create_for_own_class(self, db, reports):
        with as_user(reports["teacher"]):
            report = await get_report_service().create_report(
                db, _create(reports["child_a"], reports["class_a"], reports["template"]))
            assert report is not None

    async def test_teacher_cannot_create_for_other_class_student(self, db, reports):
        svc = get_report_service()
        with as_user(reports["teacher"]):
            # Another class's student filed under the teacher's own class.
            with pytest.raises(ValidationException):
                await svc.create_report(
                    db, _create(reports["child_b"], reports["class_a"], reports["template"]))
            with pytest.raises(ValidationException):
                await svc.create_report(
                    db, _create(reports["child_b"], reports["class_b"], reports["template"]))

    async def test_cross_tenant_ids_rejected_for_admin(self, db, reports):
        svc = get_report_service()
        with as_user(reports["admin"]):
            with pytest.raises(ValidationException):
                await svc.create_report(
                    db, _create(reports["f_student"], reports["class_a"], reports["template"]))
            with pytest.raises(ValidationException):
                await svc.create_report(
                    db, _create(reports["child_a"], reports["f_class"], reports["template"]))
            with pytest.raises(ValidationException):
                await svc.create_report(
                    db, _create(reports["child_a"], reports["class_a"], reports["f_template"]))
