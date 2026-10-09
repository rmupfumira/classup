"""PR 5 — admin notification when a parent completes signup.

Covers:
- `register_parent` fires an in-app NEW_PARENT_SIGNED_UP notification to
  every active SCHOOL_ADMIN on the tenant.
- Email channel is attempted (notify_admins is called).
- Registration never fails because a notification channel broke.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ParentInvitation, SchoolClass, Student, Tenant, User
from app.models.notification import Notification
from app.models.user import Role
from app.schemas.auth import RegisterRequest
from app.services.auth_service import AuthService
from app.services.invitation_service import get_invitation_service
from app.utils.security import hash_password
from app.utils.tenant_context import _current_user_id, _tenant_id


@pytest_asyncio.fixture
async def tenant_with_two_admins(db: AsyncSession):
    """Tenant with two active admins (admin1, admin2), one student,
    and an invitation ready to be accepted."""
    tid = uuid.uuid4()
    tenant = Tenant(
        id=tid,
        name=f"PR5 Tenant {tid.hex[:6]}",
        slug=f"pr5-{tid.hex[:8]}",
        email=f"admin@{tid.hex[:6]}.example.com",
        education_type="PRIMARY_SCHOOL",
        settings={},
        is_active=True,
        onboarding_completed=True,
    )
    db.add(tenant)
    await db.flush()

    admin1 = User(
        id=uuid.uuid4(), tenant_id=tid,
        email=f"admin1-{tid.hex[:6]}@example.com",
        password_hash=hash_password("x"),
        first_name="One", last_name="Admin",
        role=Role.SCHOOL_ADMIN.value, is_active=True,
    )
    admin2 = User(
        id=uuid.uuid4(), tenant_id=tid,
        email=f"admin2-{tid.hex[:6]}@example.com",
        password_hash=hash_password("x"),
        first_name="Two", last_name="Admin",
        role=Role.SCHOOL_ADMIN.value, is_active=True,
    )
    cls = SchoolClass(id=uuid.uuid4(), tenant_id=tid, name="Grade 1", is_active=True)
    student = Student(
        id=uuid.uuid4(), tenant_id=tid,
        first_name="Kid", last_name="Zero",
        class_id=cls.id, is_active=True,
    )
    db.add_all([admin1, admin2, cls, student])
    await db.commit()

    tok_t = _tenant_id.set(tid)
    tok_u = _current_user_id.set(admin1.id)
    try:
        yield {
            "tenant": tenant, "admin1": admin1, "admin2": admin2,
            "student": student,
        }
    finally:
        _tenant_id.reset(tok_t)
        _current_user_id.reset(tok_u)
        from sqlalchemy import text as _text
        async with db.bind.connect() as conn:
            await conn.execute(_text("DELETE FROM tenants WHERE id = :id"), {"id": tid})
            await conn.commit()


@pytest.mark.asyncio
async def test_admin_in_app_notification_fires_for_every_active_admin(
    db: AsyncSession, tenant_with_two_admins,
):
    svc = get_invitation_service()
    inv = await svc.create_invitation(
        db,
        student_id=tenant_with_two_admins["student"].id,
        email="newparent@example.com",
        first_name="New",
        last_name="Parent",
    )

    # Stub email so we don't try to open an SMTP socket in tests.
    with patch(
        "app.services.email_service.EmailService.notify_admins",
        new=AsyncMock(return_value=None),
    ):
        await AuthService().register_parent(db, RegisterRequest(
            invitation_code=inv.invitation_code,
            email="newparent@example.com",
            password="s3cretpa$$",
            confirm_password="s3cretpa$$",
            first_name="New",
            last_name="Parent",
            phone="+263771112222",
            whatsapp_opt_in=False,
            email_opt_in=True,
        ))

    tid = tenant_with_two_admins["tenant"].id
    rows = (await db.execute(
        select(Notification).where(
            Notification.tenant_id == tid,
            Notification.notification_type == "NEW_PARENT_SIGNED_UP",
        )
    )).scalars().all()
    user_ids = {r.user_id for r in rows}
    assert tenant_with_two_admins["admin1"].id in user_ids
    assert tenant_with_two_admins["admin2"].id in user_ids
    # And the notification should name the new parent so the admin can
    # grok it from the chip alone.
    assert any("New Parent" in r.title for r in rows)


@pytest.mark.asyncio
async def test_email_channel_invoked_once_for_the_tenant(
    db: AsyncSession, tenant_with_two_admins,
):
    """The email path uses the generic notify_admins broadcast, which
    fans out internally — we only need to see it fire once."""
    svc = get_invitation_service()
    inv = await svc.create_invitation(
        db,
        student_id=tenant_with_two_admins["student"].id,
        email="newparent2@example.com",
        first_name="Also",
        last_name="New",
    )
    with patch(
        "app.services.email_service.EmailService.notify_admins",
        new=AsyncMock(return_value=None),
    ) as notify_mock:
        await AuthService().register_parent(db, RegisterRequest(
            invitation_code=inv.invitation_code,
            email="newparent2@example.com",
            password="s3cretpa$$",
            confirm_password="s3cretpa$$",
            first_name="Also",
            last_name="New",
            phone="+263772223333",
            whatsapp_opt_in=False,
            email_opt_in=True,
        ))
    notify_mock.assert_awaited_once()
    kwargs = notify_mock.await_args.kwargs
    assert kwargs["notification_type"] == "NEW_PARENT_SIGNED_UP"


@pytest.mark.asyncio
async def test_registration_survives_notification_failure(
    db: AsyncSession, tenant_with_two_admins,
):
    """A notification failure must not undo the user record. The whole
    helper is wrapped in try/except at the top level — this test proves
    that contract."""
    svc = get_invitation_service()
    inv = await svc.create_invitation(
        db,
        student_id=tenant_with_two_admins["student"].id,
        email="newparent3@example.com",
        first_name="Resilient",
        last_name="Parent",
    )

    # Make every single downstream path blow up.
    with patch(
        "app.services.email_service.EmailService.notify_admins",
        new=AsyncMock(side_effect=RuntimeError("email boom")),
    ), patch(
        "app.services.notification_service.NotificationService.create_bulk_notifications",
        new=AsyncMock(side_effect=RuntimeError("db boom")),
    ):
        # This must not raise.
        resp = await AuthService().register_parent(db, RegisterRequest(
            invitation_code=inv.invitation_code,
            email="newparent3@example.com",
            password="s3cretpa$$",
            confirm_password="s3cretpa$$",
            first_name="Resilient",
            last_name="Parent",
            phone="+263773334444",
            whatsapp_opt_in=False,
            email_opt_in=True,
        ))

    # User record was created.
    user = (await db.execute(
        select(User).where(User.email == "newparent3@example.com")
    )).scalar_one()
    assert user.id == resp.user_id
