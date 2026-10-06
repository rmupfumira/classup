"""Multi-tenant login — same email across two schools.

Owner directive 2026-10-06: an email registered at multiple tenants
must be resolvable at login — either by tenant_slug on the request,
or by surfacing a chooser via MultipleTenantsException.
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import UnauthorizedException
from app.models import Tenant, User
from app.models.user import Role
from app.schemas.auth import LoginRequest
from app.services.auth_service import AuthService, MultipleTenantsException
from app.utils.security import hash_password


@pytest.fixture
async def two_tenants_same_email(db: AsyncSession):
    """Build two tenants where the same email is a user at both."""
    email = f"parent-{uuid.uuid4().hex[:8]}@example.com"
    password = "s3cretpa$$"

    t1 = Tenant(
        id=uuid.uuid4(),
        name=f"School Alpha {uuid.uuid4().hex[:4]}",
        slug=f"school-alpha-{uuid.uuid4().hex[:8]}",
        email=f"admin@{uuid.uuid4().hex[:6]}.test",
        education_type="PRIMARY_SCHOOL",
        settings={},
        is_active=True,
        onboarding_completed=True,
    )
    t2 = Tenant(
        id=uuid.uuid4(),
        name=f"School Beta {uuid.uuid4().hex[:4]}",
        slug=f"school-beta-{uuid.uuid4().hex[:8]}",
        email=f"admin@{uuid.uuid4().hex[:6]}.test",
        education_type="PRIMARY_SCHOOL",
        settings={},
        is_active=True,
        onboarding_completed=True,
    )
    db.add_all([t1, t2])
    await db.commit()

    pw_hash = hash_password(password)
    u1 = User(
        id=uuid.uuid4(),
        tenant_id=t1.id,
        email=email,
        password_hash=pw_hash,
        first_name="Pat",
        last_name="Parent",
        role=Role.PARENT.value,
        is_active=True,
        language="en",
    )
    u2 = User(
        id=uuid.uuid4(),
        tenant_id=t2.id,
        email=email,
        password_hash=pw_hash,
        first_name="Pat",
        last_name="Parent",
        role=Role.PARENT.value,
        is_active=True,
        language="en",
    )
    db.add_all([u1, u2])
    await db.commit()

    yield {"email": email, "password": password, "t1": t1, "t2": t2}

    # Cleanup
    from sqlalchemy import delete
    await db.execute(delete(User).where(User.id.in_([u1.id, u2.id])))
    await db.execute(delete(Tenant).where(Tenant.id.in_([t1.id, t2.id])))
    await db.commit()


@pytest.mark.asyncio
async def test_login_without_slug_raises_multi_tenant_when_two_match(
    db: AsyncSession, two_tenants_same_email
):
    svc = AuthService()
    req = LoginRequest(
        email=two_tenants_same_email["email"],
        password=two_tenants_same_email["password"],
    )
    with pytest.raises(MultipleTenantsException) as exc_info:
        await svc.login(db, req)
    choices = exc_info.value.tenants
    assert len(choices) == 2
    slugs = {slug for slug, _ in choices}
    assert two_tenants_same_email["t1"].slug in slugs
    assert two_tenants_same_email["t2"].slug in slugs


@pytest.mark.asyncio
async def test_login_with_slug_scopes_to_that_tenant(
    db: AsyncSession, two_tenants_same_email
):
    svc = AuthService()
    req = LoginRequest(
        email=two_tenants_same_email["email"],
        password=two_tenants_same_email["password"],
        tenant_slug=two_tenants_same_email["t1"].slug,
    )
    resp, user = await svc.login(db, req)
    assert user.tenant_id == two_tenants_same_email["t1"].id
    assert resp.access_token


@pytest.mark.asyncio
async def test_login_with_wrong_slug_fails(
    db: AsyncSession, two_tenants_same_email
):
    svc = AuthService()
    req = LoginRequest(
        email=two_tenants_same_email["email"],
        password=two_tenants_same_email["password"],
        tenant_slug="nonexistent-school",
    )
    with pytest.raises(UnauthorizedException):
        await svc.login(db, req)


@pytest.mark.asyncio
async def test_login_single_tenant_unchanged(
    db: AsyncSession, test_tenant: Tenant
):
    """Existing single-tenant login must still work without a slug."""
    email = f"single-{uuid.uuid4().hex[:8]}@example.com"
    password = "s3cretpa$$"
    user = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email=email,
        password_hash=hash_password(password),
        first_name="Only",
        last_name="One",
        role=Role.PARENT.value,
        is_active=True,
        language="en",
    )
    db.add(user)
    await db.commit()

    svc = AuthService()
    req = LoginRequest(email=email, password=password)
    resp, logged_in_user = await svc.login(db, req)
    assert logged_in_user.id == user.id
    assert resp.access_token
