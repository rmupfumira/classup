"""Tests for the parent-editable medical info flow (2026-11-18).

A PARENT can patch their own child's medical_info, allergies and
emergency_contacts via `/api/v1/students/{id}/parent-editable`. Guards:
- Must be linked via parent_students.
- Only the 3 fields are writable; everything else on the Student row
  is read-only to a parent.
"""

from __future__ import annotations

import uuid
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ForbiddenException
from app.models import Student, Tenant, User
from app.models.student import ParentStudent
from app.schemas.student import StudentParentEditable
from app.services.student_service import get_student_service
from app.utils.security import hash_password
from app.utils.tenant_context import _tenant_id as tctx


@pytest_asyncio.fixture
async def linked_tenant(db: AsyncSession):
    """Tenant + a parent-user linked to a student, so the happy path
    has data to exercise."""
    tid = uuid.uuid4()
    t = Tenant(
        id=tid, name="Audit School",
        slug=f"audit-{tid.hex[:6]}",
        email=f"a@{tid.hex[:6]}.test",
        education_type="PRIMARY_SCHOOL",
        settings={}, is_active=True, onboarding_completed=True,
    )
    db.add(t); await db.commit(); await db.refresh(t)

    parent = User(
        id=uuid.uuid4(), tenant_id=t.id,
        email=f"parent-{t.id.hex[:6]}@test.local",
        password_hash=hash_password("x"),
        first_name="Pat", last_name="Doe",
        role="PARENT", is_active=True, language="en",
    )
    other_parent = User(
        id=uuid.uuid4(), tenant_id=t.id,
        email=f"other-{t.id.hex[:6]}@test.local",
        password_hash=hash_password("x"),
        first_name="Nora", last_name="Other",
        role="PARENT", is_active=True, language="en",
    )
    student = Student(
        id=uuid.uuid4(), tenant_id=t.id,
        first_name="Sam", last_name="Doe", is_active=True,
    )
    other_student = Student(
        id=uuid.uuid4(), tenant_id=t.id,
        first_name="Ava", last_name="Other", is_active=True,
    )
    db.add_all([parent, other_parent, student, other_student])
    await db.commit()
    for row in (parent, other_parent, student, other_student):
        await db.refresh(row)

    db.add(ParentStudent(
        parent_id=parent.id, student_id=student.id,
        relationship_type="PARENT", is_primary=True,
    ))
    await db.commit()

    tok = tctx.set(t.id)
    try:
        yield {"tenant": t, "parent": parent, "student": student,
               "other_parent": other_parent, "other_student": other_student}
    finally:
        tctx.reset(tok)
        await db.execute(delete(Tenant).where(Tenant.id == t.id))
        await db.commit()


@pytest.mark.asyncio
async def test_parent_can_update_own_childs_medical(
    db: AsyncSession, linked_tenant,
):
    """Happy path — a linked parent updates their child's medical info."""
    svc = get_student_service()
    student = linked_tenant["student"]
    parent = linked_tenant["parent"]

    payload = StudentParentEditable(
        medical_info="Asthma — ventolin in school bag",
        allergies="Peanuts (Epipen)",
        emergency_contacts=[],
    )
    out = await svc.update_student_parent_editable(
        db, student.id, parent.id, payload,
    )
    assert out.medical_info == "Asthma — ventolin in school bag"
    assert out.allergies == "Peanuts (Epipen)"


@pytest.mark.asyncio
async def test_parent_cannot_update_someone_elses_child(
    db: AsyncSession, linked_tenant,
):
    """A parent NOT linked to a student gets ForbiddenException, not
    a NotFoundException — we don't want to leak existence."""
    svc = get_student_service()
    other_student = linked_tenant["other_student"]
    parent = linked_tenant["parent"]  # linked only to student, not other_student

    payload = StudentParentEditable(allergies="malicious edit")
    with pytest.raises(ForbiddenException):
        await svc.update_student_parent_editable(
            db, other_student.id, parent.id, payload,
        )


@pytest.mark.asyncio
async def test_parent_cannot_edit_non_whitelisted_fields(
    db: AsyncSession, linked_tenant,
):
    """StudentParentEditable has only 3 fields. Extra fields in a dict
    don't make it onto the model. Guards against a malicious client
    smuggling `first_name` or `class_id` via the parent endpoint.
    """
    svc = get_student_service()
    student = linked_tenant["student"]
    parent = linked_tenant["parent"]
    original_name = student.first_name

    # Pydantic validates: extra fields are dropped by default (ignore).
    payload = StudentParentEditable.model_validate({
        "medical_info": "updated",
        "first_name": "Evil",
        "class_id": str(uuid.uuid4()),
    })
    await svc.update_student_parent_editable(db, student.id, parent.id, payload)
    await db.refresh(student)
    assert student.medical_info == "updated"
    assert student.first_name == original_name, (
        "parent must not be able to rename a student via this endpoint"
    )
