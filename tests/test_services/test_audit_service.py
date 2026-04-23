"""Tests for the audit-log service."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, Tenant, User
from app.models.user import Role
from app.services.audit_service import AUDIT_CONFIG_KEY, get_audit_service
from app.utils.security import hash_password


class TestAuditConfig:
    async def test_default_config_returned_when_none_stored(self, db: AsyncSession):
        service = get_audit_service()
        cfg = await service.get_config(db)
        assert cfg["enabled"] is True
        assert cfg["level"] == "STANDARD"
        assert cfg["retention_days"] == 90

    async def test_update_config_persists(self, db: AsyncSession):
        service = get_audit_service()
        await service.update_config(db, level="VERBOSE", retention_days=30)
        await db.commit()

        fresh = await service.get_config(db)
        assert fresh["level"] == "VERBOSE"
        assert fresh["retention_days"] == 30

    async def test_invalid_level_rejected(self, db: AsyncSession):
        service = get_audit_service()
        with pytest.raises(ValueError):
            await service.update_config(db, level="SHOUTING")

    async def test_invalid_retention_rejected(self, db: AsyncSession):
        service = get_audit_service()
        with pytest.raises(ValueError):
            await service.update_config(db, retention_days=0)
        with pytest.raises(ValueError):
            await service.update_config(db, retention_days=5000)


class TestAuditWriteAndRead:
    async def test_log_event_writes_row(self, db: AsyncSession, test_tenant: Tenant):
        service = get_audit_service()
        entry = await service.log_event(
            db,
            action="students.create",
            tenant_id=test_tenant.id,
            tenant_name=test_tenant.name,
            user_email="tester@example.com",
            user_name="Tester",
            user_role="SCHOOL_ADMIN",
            method="POST",
            path="/api/v1/students",
            status_code=201,
            ip_address="10.0.0.1",
        )
        await db.commit()
        assert entry.id is not None
        assert entry.action == "students.create"
        assert entry.user_email == "tester@example.com"

    async def test_list_events_filters_and_paginates(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        service = get_audit_service()
        for i in range(5):
            await service.log_event(
                db,
                action=f"students.create" if i % 2 == 0 else "students.view",
                tenant_id=test_tenant.id,
                tenant_name=test_tenant.name,
            )
        await db.commit()

        # Filter by action prefix
        created, _ = await service.list_events(
            db, tenant_id=test_tenant.id, action="create"
        )
        assert all("create" in e.action for e in created)

        # Pagination
        items, total = await service.list_events(
            db, tenant_id=test_tenant.id, page=1, page_size=2
        )
        assert len(items) == 2
        assert total >= 5

    async def test_list_events_orders_newest_first(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        service = get_audit_service()
        await service.log_event(db, action="first", tenant_id=test_tenant.id)
        await service.log_event(db, action="second", tenant_id=test_tenant.id)
        await service.log_event(db, action="third", tenant_id=test_tenant.id)
        await db.commit()

        items, _ = await service.list_events(
            db, tenant_id=test_tenant.id, page_size=3
        )
        # Newest first — the most recently inserted 'third' should be index 0
        actions = [e.action for e in items[:3]]
        assert actions[0] == "third"


class TestOnlineUsers:
    async def test_online_users_returns_users_in_window(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        service = get_audit_service()
        user = User(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            email=f"u-{uuid.uuid4().hex[:6]}@test.local",
            password_hash=hash_password("x"),
            first_name="On",
            last_name="Line",
            role=Role.SCHOOL_ADMIN.value,
            is_active=True,
            language="en",
        )
        db.add(user)
        await db.commit()

        # Log a recent event for the user
        await service.log_event(
            db,
            action="students.view",
            user_id=user.id,
            user_email=user.email,
            user_name=f"{user.first_name} {user.last_name}",
            user_role=user.role,
            tenant_id=test_tenant.id,
            tenant_name=test_tenant.name,
        )
        await db.commit()

        online = await service.get_online_users(db, threshold_minutes=5)
        assert any(u["user_id"] == str(user.id) for u in online)

    async def test_online_users_excludes_old_activity(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        service = get_audit_service()
        user = User(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            email=f"old-{uuid.uuid4().hex[:6]}@test.local",
            password_hash=hash_password("x"),
            first_name="Off",
            last_name="Line",
            role=Role.TEACHER.value,
            is_active=True,
            language="en",
        )
        db.add(user)
        await db.commit()

        # Insert an old event directly (bypass log_event since it uses server default)
        old = AuditLog(
            action="students.view",
            user_id=user.id,
            user_email=user.email,
            tenant_id=test_tenant.id,
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        db.add(old)
        await db.commit()

        online = await service.get_online_users(db, threshold_minutes=5)
        assert not any(u["user_id"] == str(user.id) for u in online)


class TestPurge:
    async def test_purge_removes_old_entries(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        service = get_audit_service()
        # Old entry
        old = AuditLog(
            action="something.old",
            tenant_id=test_tenant.id,
            created_at=datetime.now(timezone.utc) - timedelta(days=100),
        )
        # Recent entry
        recent = AuditLog(
            action="something.recent",
            tenant_id=test_tenant.id,
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        db.add_all([old, recent])
        await db.commit()

        deleted = await service.purge_old(db, retention_days=30)
        await db.commit()

        assert deleted >= 1
        # The recent one should survive
        items, _ = await service.list_events(
            db, tenant_id=test_tenant.id, action="something"
        )
        actions = [e.action for e in items]
        assert "something.old" not in actions
        assert "something.recent" in actions
