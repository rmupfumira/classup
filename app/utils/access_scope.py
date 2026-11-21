"""Role-level row scoping shared by services.

Tenant isolation is applied by each caller; these helpers add the
second layer from the permission matrix: teachers see their own
classes, parents see their own linked children, admins see the tenant.
"""

import uuid

from sqlalchemy import false, select

from app.models.academic import ClassSubjectTeacher
from app.models.school_class import SchoolClass, TeacherClass
from app.models.student import ParentStudent, Student
from app.models.tenant import Tenant
from app.models.user import Role
from app.utils.tenant_context import get_current_user_id, get_current_user_role


def teacher_class_ids(teacher_id: uuid.UUID):
    """Subquery of classes the teacher is a general class teacher of.

    Subject-teacher rows (ClassSubjectTeacher) deliberately don't count:
    they grant access to that one subject's marking, not the whole class.
    """
    return select(TeacherClass.class_id).where(TeacherClass.teacher_id == teacher_id)


def subject_teacher_pairs(teacher_id: uuid.UUID):
    """Subquery of (class_id, subject_id) the teacher marks as a subject
    teacher, only while the tenant has subject-teacher mode on."""
    return (
        select(ClassSubjectTeacher.class_id, ClassSubjectTeacher.subject_id)
        .join(SchoolClass, SchoolClass.id == ClassSubjectTeacher.class_id)
        .join(Tenant, Tenant.id == SchoolClass.tenant_id)
        .where(
            ClassSubjectTeacher.teacher_id == teacher_id,
            Tenant.settings["subject_teacher_enabled"].as_boolean().is_(True),
        )
    )


def parent_student_ids(parent_id: uuid.UUID):
    """Subquery of student ids linked to the parent."""
    return select(ParentStudent.student_id).where(ParentStudent.parent_id == parent_id)


def viewer_student_ids():
    """Subquery of student ids the current user may see, or None for
    unrestricted roles (admins, super admins, background jobs)."""
    role = get_current_user_role()
    if role == Role.TEACHER.value:
        return select(Student.id).where(
            Student.class_id.in_(teacher_class_ids(get_current_user_id()))
        )
    if role == Role.PARENT.value:
        return parent_student_ids(get_current_user_id())
    return None


def scope_to_viewer(query, *, student_col=None, class_col=None):
    """Restrict ``query`` to rows the current user may see.

    Teachers are matched on ``class_col`` when given (falling back to
    the student's current class), parents on ``student_col``. A role
    whose column isn't supplied is denied everything rather than
    silently seeing the whole tenant.
    """
    role = get_current_user_role()
    if role == Role.TEACHER.value:
        if class_col is not None:
            return query.where(class_col.in_(teacher_class_ids(get_current_user_id())))
        if student_col is not None:
            return query.where(student_col.in_(viewer_student_ids()))
        return query.where(false())
    if role == Role.PARENT.value:
        if student_col is not None:
            return query.where(student_col.in_(parent_student_ids(get_current_user_id())))
        return query.where(false())
    return query
