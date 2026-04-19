"""Shared pytest fixtures for ClassUp tests.

Strategy: create isolated test data (tenant, users, class, subjects) per test
module. Tests rely on ON DELETE CASCADE for cleanup when the tenant is removed
in the teardown.
"""

import uuid
from typing import AsyncGenerator

import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models import (
    ClassSubject,
    SchoolClass,
    Student,
    Subject,
    TeacherClass,
    Tenant,
    User,
)
from app.models.student import ParentStudent
from app.models.user import Role
from app.utils.security import hash_password
from app.utils.tenant_context import (
    _current_user_id,
    _current_user_role,
    _tenant_id,
)


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    """Provide a database session for each test."""
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
            await session.close()


@pytest_asyncio.fixture
async def test_tenant(db: AsyncSession) -> AsyncGenerator[Tenant, None]:
    """Create an isolated test tenant with timetable_management enabled."""
    tenant_id = uuid.uuid4()
    slug = f"test-tenant-{tenant_id.hex[:8]}"
    tenant = Tenant(
        id=tenant_id,
        name=f"Test School {tenant_id.hex[:6]}",
        slug=slug,
        email=f"admin@{slug}.test",
        education_type="PRIMARY_SCHOOL",
        settings={
            "features": {
                "timetable_management": True,
                "attendance_tracking": True,
                "messaging": True,
            },
            "education_type": "PRIMARY_SCHOOL",
        },
        is_active=True,
        onboarding_completed=True,
    )
    db.add(tenant)
    await db.commit()
    await db.refresh(tenant)

    # Set tenant context so services can find it
    tenant_token = _tenant_id.set(tenant.id)

    try:
        yield tenant
    finally:
        _tenant_id.reset(tenant_token)
        # Delete tenant — CASCADE cleans up users, classes, subjects,
        # timetables, entries, etc.
        await db.execute(delete(Tenant).where(Tenant.id == tenant.id))
        await db.commit()


@pytest_asyncio.fixture
async def test_admin(db: AsyncSession, test_tenant: Tenant) -> AsyncGenerator[User, None]:
    """Create a school admin user for the test tenant."""
    admin = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email=f"admin-{uuid.uuid4().hex[:8]}@test.local",
        password_hash=hash_password("testpass123"),
        first_name="Test",
        last_name="Admin",
        role=Role.SCHOOL_ADMIN.value,
        is_active=True,
        language="en",
    )
    db.add(admin)
    await db.commit()
    await db.refresh(admin)

    user_token = _current_user_id.set(admin.id)
    role_token = _current_user_role.set(admin.role)

    try:
        yield admin
    finally:
        _current_user_id.reset(user_token)
        _current_user_role.reset(role_token)


@pytest_asyncio.fixture
async def test_teacher(db: AsyncSession, test_tenant: Tenant) -> User:
    """Create a teacher user."""
    teacher = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email=f"teacher-{uuid.uuid4().hex[:8]}@test.local",
        password_hash=hash_password("testpass123"),
        first_name="Jane",
        last_name="Teacher",
        role=Role.TEACHER.value,
        is_active=True,
        language="en",
    )
    db.add(teacher)
    await db.commit()
    await db.refresh(teacher)
    return teacher


@pytest_asyncio.fixture
async def test_class(db: AsyncSession, test_tenant: Tenant) -> SchoolClass:
    """Create a school class."""
    cls = SchoolClass(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        name="Grade 5A",
        description="Test class",
        is_active=True,
    )
    db.add(cls)
    await db.commit()
    await db.refresh(cls)
    return cls


@pytest_asyncio.fixture
async def test_class_with_subjects(
    db: AsyncSession,
    test_tenant: Tenant,
    test_class: SchoolClass,
    test_teacher: User,
) -> dict:
    """Create a class with 3 subjects and a primary teacher assigned."""
    subjects = []
    for name in ("Mathematics", "English", "Science"):
        s = Subject(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            name=name,
            code=f"{name[:3].upper()}-{uuid.uuid4().hex[:4]}",
            is_active=True,
        )
        db.add(s)
        subjects.append(s)
    await db.commit()

    for i, s in enumerate(subjects):
        cs = ClassSubject(
            id=uuid.uuid4(),
            class_id=test_class.id,
            subject_id=s.id,
            is_compulsory=True,
            display_order=i,
        )
        db.add(cs)

    # Assign teacher as primary
    tc = TeacherClass(
        id=uuid.uuid4(),
        teacher_id=test_teacher.id,
        class_id=test_class.id,
        is_primary=True,
    )
    db.add(tc)
    await db.commit()

    return {
        "tenant": test_tenant,
        "class": test_class,
        "subjects": subjects,
        "teacher": test_teacher,
    }
