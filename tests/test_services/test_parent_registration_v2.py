"""Parent registration — 2026-10-09 onboarding redesign.

Covers:
- Invitation now carries ``parent_phone`` + ``suggest_whatsapp_opt_in``
  from the admin form through to the registration page.
- Registration requires a phone (previously optional).
- When the parent ticks WhatsApp opt-in, ``whatsapp_phone`` is set
  from the same mobile number and ``whatsapp_opted_in`` is True.
- When the parent unticks WhatsApp, neither flag is set.
- ``email_opted_in`` is True by default; a parent can opt out of email.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ValidationException
from app.models import ParentInvitation, SchoolClass, Student, Tenant, User
from app.models.user import Role
from app.schemas.auth import RegisterRequest
from app.services.auth_service import AuthService
from app.services.invitation_service import get_invitation_service
from app.utils.security import hash_password
from app.utils.tenant_context import _current_user_id, _tenant_id


@pytest_asyncio.fixture
async def tenant_with_admin(db: AsyncSession):
    """A tenant + a school admin to be the invitation.created_by."""
    tid = uuid.uuid4()
    tenant = Tenant(
        id=tid,
        name=f"Reg Test {tid.hex[:6]}",
        slug=f"reg-test-{tid.hex[:8]}",
        email=f"admin@{tid.hex[:6]}.example.com",
        education_type="PRIMARY_SCHOOL",
        settings={},
        is_active=True,
        onboarding_completed=True,
    )
    db.add(tenant)
    await db.flush()

    admin = User(
        id=uuid.uuid4(), tenant_id=tid,
        email=f"admin-{tid.hex[:6]}@example.com",
        password_hash=hash_password("x"),
        first_name="Ad", last_name="Min",
        role=Role.SCHOOL_ADMIN.value, is_active=True,
    )
    db.add(admin)

    cls = SchoolClass(id=uuid.uuid4(), tenant_id=tid, name="Grade 1", is_active=True)
    student = Student(
        id=uuid.uuid4(), tenant_id=tid,
        first_name="Asher", last_name="Mupfumira",
        class_id=cls.id, is_active=True,
    )
    db.add_all([cls, student])
    await db.commit()

    tok_t = _tenant_id.set(tid)
    tok_u = _current_user_id.set(admin.id)
    try:
        yield {"tenant": tenant, "admin": admin, "student": student}
    finally:
        _tenant_id.reset(tok_t)
        _current_user_id.reset(tok_u)
        from sqlalchemy import text as _text
        async with db.bind.connect() as conn:
            await conn.execute(_text("DELETE FROM tenants WHERE id = :id"), {"id": tid})
            await conn.commit()


# ---------------------------------------------------------------------------
# 1. Invitation carries new fields through
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invitation_persists_parent_phone_and_whatsapp_hint(
    db: AsyncSession, tenant_with_admin
):
    svc = get_invitation_service()
    inv = await svc.create_invitation(
        db,
        student_id=tenant_with_admin["student"].id,
        email="parent@example.com",
        first_name="Pat",
        last_name="Parent",
        parent_phone="+263771234567",
        suggest_whatsapp_opt_in=True,
    )
    assert inv.parent_phone == "+263771234567"
    assert inv.suggest_whatsapp_opt_in is True


@pytest.mark.asyncio
async def test_invitation_default_values_backwards_compat(
    db: AsyncSession, tenant_with_admin
):
    """Callers that don't pass the new fields get the old behaviour."""
    svc = get_invitation_service()
    inv = await svc.create_invitation(
        db,
        student_id=tenant_with_admin["student"].id,
        email="parent@example.com",
        first_name="Pat",
        last_name="Parent",
    )
    assert inv.parent_phone is None
    assert inv.suggest_whatsapp_opt_in is False


# ---------------------------------------------------------------------------
# 2. Register requires phone
# ---------------------------------------------------------------------------

def test_register_request_requires_phone():
    with pytest.raises(Exception):
        RegisterRequest(
            invitation_code="ABCD1234",
            email="pat@example.com",
            password="s3cretpa$$",
            confirm_password="s3cretpa$$",
            first_name="Pat",
            last_name="Parent",
            phone="",  # empty should be rejected
        )


def test_register_request_rejects_local_phone_format():
    """Non-E.164 input is normalised, else rejected."""
    with pytest.raises(Exception):
        RegisterRequest(
            invitation_code="ABCD1234",
            email="pat@example.com",
            password="s3cretpa$$",
            confirm_password="s3cretpa$$",
            first_name="Pat",
            last_name="Parent",
            phone="0771234567",  # local — no country code
        )


def test_register_request_accepts_e164():
    req = RegisterRequest(
        invitation_code="ABCD1234",
        email="pat@example.com",
        password="s3cretpa$$",
        confirm_password="s3cretpa$$",
        first_name="Pat",
        last_name="Parent",
        phone="+263 77 123 4567",
    )
    assert req.phone == "+263771234567"


# ---------------------------------------------------------------------------
# 3. Opt-in flags land on the user row
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_register_with_whatsapp_opt_in_sets_both_flags(
    db: AsyncSession, tenant_with_admin
):
    svc = get_invitation_service()
    inv = await svc.create_invitation(
        db,
        student_id=tenant_with_admin["student"].id,
        email="pat1@example.com",
        first_name="Pat",
        last_name="Parent",
        parent_phone="+263771111111",
        suggest_whatsapp_opt_in=True,
    )
    auth = AuthService()
    await auth.register_parent(db, RegisterRequest(
        invitation_code=inv.invitation_code,
        email="pat1@example.com",
        password="s3cretpa$$",
        confirm_password="s3cretpa$$",
        first_name="Pat",
        last_name="Parent",
        phone="+263771111111",
        whatsapp_opt_in=True,
        email_opt_in=True,
    ))
    user = (await db.execute(
        select(User).where(User.email == "pat1@example.com")
    )).scalar_one()
    assert user.phone == "+263771111111"
    assert user.whatsapp_phone == "+263771111111"
    assert user.whatsapp_opted_in is True
    assert user.email_opted_in is True


@pytest.mark.asyncio
async def test_register_without_whatsapp_opt_in_leaves_whatsapp_fields_blank(
    db: AsyncSession, tenant_with_admin
):
    svc = get_invitation_service()
    inv = await svc.create_invitation(
        db,
        student_id=tenant_with_admin["student"].id,
        email="pat2@example.com",
        first_name="Pat",
        last_name="Parent",
    )
    auth = AuthService()
    await auth.register_parent(db, RegisterRequest(
        invitation_code=inv.invitation_code,
        email="pat2@example.com",
        password="s3cretpa$$",
        confirm_password="s3cretpa$$",
        first_name="Pat",
        last_name="Parent",
        phone="+263772222222",
        whatsapp_opt_in=False,
        email_opt_in=True,
    ))
    user = (await db.execute(
        select(User).where(User.email == "pat2@example.com")
    )).scalar_one()
    assert user.phone == "+263772222222"
    assert user.whatsapp_phone is None
    assert user.whatsapp_opted_in is False
    assert user.email_opted_in is True


@pytest.mark.asyncio
async def test_register_can_opt_out_of_email(
    db: AsyncSession, tenant_with_admin
):
    """Parents can decline email too; the per-channel gate honours it."""
    svc = get_invitation_service()
    inv = await svc.create_invitation(
        db,
        student_id=tenant_with_admin["student"].id,
        email="pat3@example.com",
        first_name="Pat",
        last_name="Parent",
    )
    auth = AuthService()
    await auth.register_parent(db, RegisterRequest(
        invitation_code=inv.invitation_code,
        email="pat3@example.com",
        password="s3cretpa$$",
        confirm_password="s3cretpa$$",
        first_name="Pat",
        last_name="Parent",
        phone="+263773333333",
        whatsapp_opt_in=True,
        email_opt_in=False,
    ))
    user = (await db.execute(
        select(User).where(User.email == "pat3@example.com")
    )).scalar_one()
    assert user.email_opted_in is False
    assert user.whatsapp_opted_in is True
