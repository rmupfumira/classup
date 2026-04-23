"""Audit log service — writes, queries, and maintains audit events."""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, delete, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLevel, AuditLog, SystemSettings

logger = logging.getLogger(__name__)

AUDIT_CONFIG_KEY = "audit_config"

DEFAULT_CONFIG = {
    "enabled": True,
    "level": AuditLevel.STANDARD.value,
    "retention_days": 90,
}


class AuditService:
    """Read, write, and purge audit log entries."""

    # ------------- Config -------------

    async def get_config(self, db: AsyncSession) -> dict:
        """Return the audit config, falling back to defaults."""
        result = await db.execute(
            select(SystemSettings).where(SystemSettings.key == AUDIT_CONFIG_KEY)
        )
        row = result.scalar_one_or_none()
        if not row or not row.value:
            return dict(DEFAULT_CONFIG)
        merged = dict(DEFAULT_CONFIG)
        merged.update(row.value or {})
        return merged

    async def update_config(
        self,
        db: AsyncSession,
        enabled: bool | None = None,
        level: str | None = None,
        retention_days: int | None = None,
    ) -> dict:
        """Update the audit config (super admin)."""
        current = await self.get_config(db)
        if enabled is not None:
            current["enabled"] = bool(enabled)
        if level is not None:
            lv = level.strip().upper()
            if lv not in {AuditLevel.MINIMAL.value, AuditLevel.STANDARD.value, AuditLevel.VERBOSE.value}:
                raise ValueError(f"Invalid level: {level}")
            current["level"] = lv
        if retention_days is not None:
            days = int(retention_days)
            if days < 1 or days > 3650:
                raise ValueError("retention_days must be between 1 and 3650")
            current["retention_days"] = days

        # Upsert
        result = await db.execute(
            select(SystemSettings).where(SystemSettings.key == AUDIT_CONFIG_KEY)
        )
        row = result.scalar_one_or_none()
        if row:
            row.value = current
        else:
            row = SystemSettings(key=AUDIT_CONFIG_KEY, value=current)
            db.add(row)
        await db.flush()
        return current

    # ------------- Write -------------

    async def log_event(
        self,
        db: AsyncSession,
        *,
        action: str,
        user_id: uuid.UUID | None = None,
        user_email: str | None = None,
        user_name: str | None = None,
        user_role: str | None = None,
        tenant_id: uuid.UUID | None = None,
        tenant_name: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        method: str | None = None,
        path: str | None = None,
        status_code: int | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditLog:
        """Write an audit event directly (when you have a DB session)."""
        # Trim long strings defensively
        def clip(s: str | None, n: int) -> str | None:
            if s is None:
                return None
            return s[:n]

        entry = AuditLog(
            action=action[:80],
            user_id=user_id,
            user_email=clip(user_email, 255),
            user_name=clip(user_name, 200),
            user_role=clip(user_role, 30),
            tenant_id=tenant_id,
            tenant_name=clip(tenant_name, 255),
            resource_type=clip(resource_type, 60),
            resource_id=clip(resource_id, 100),
            method=clip(method, 10),
            path=clip(path, 500),
            status_code=status_code,
            ip_address=clip(ip_address, 45),
            user_agent=clip(user_agent, 500),
            details=details,
        )
        db.add(entry)
        await db.flush()
        return entry

    # ------------- Read -------------

    async def list_events(
        self,
        db: AsyncSession,
        tenant_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        since: datetime | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[AuditLog], int]:
        """Filterable list of audit events, newest first."""
        stmt = select(AuditLog)
        if tenant_id:
            stmt = stmt.where(AuditLog.tenant_id == tenant_id)
        if user_id:
            stmt = stmt.where(AuditLog.user_id == user_id)
        if action:
            stmt = stmt.where(AuditLog.action.ilike(f"%{action}%"))
        if resource_type:
            stmt = stmt.where(AuditLog.resource_type == resource_type)
        if since:
            stmt = stmt.where(AuditLog.created_at >= since)
        if search:
            q = f"%{search}%"
            stmt = stmt.where(
                or_(
                    AuditLog.user_email.ilike(q),
                    AuditLog.user_name.ilike(q),
                    AuditLog.tenant_name.ilike(q),
                    AuditLog.action.ilike(q),
                    AuditLog.path.ilike(q),
                )
            )

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await db.execute(count_stmt)).scalar() or 0

        stmt = stmt.order_by(desc(AuditLog.created_at))
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        result = await db.execute(stmt)
        return list(result.scalars().all()), total

    async def get_online_users(
        self, db: AsyncSession, threshold_minutes: int = 5
    ) -> list[dict]:
        """Return users with any activity in the last N minutes.

        Groups by user_id, keeps the most recent event per user, and
        returns a list sorted by most-recent first.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=threshold_minutes)
        stmt = (
            select(
                AuditLog.user_id,
                AuditLog.user_email,
                AuditLog.user_name,
                AuditLog.user_role,
                AuditLog.tenant_id,
                AuditLog.tenant_name,
                AuditLog.ip_address,
                func.max(AuditLog.created_at).label("last_seen_at"),
                func.count(AuditLog.id).label("event_count"),
            )
            .where(
                and_(
                    AuditLog.created_at >= cutoff,
                    AuditLog.user_id.is_not(None),
                )
            )
            .group_by(
                AuditLog.user_id,
                AuditLog.user_email,
                AuditLog.user_name,
                AuditLog.user_role,
                AuditLog.tenant_id,
                AuditLog.tenant_name,
                AuditLog.ip_address,
            )
            .order_by(desc("last_seen_at"))
        )
        result = await db.execute(stmt)
        rows = result.all()
        return [
            {
                "user_id": str(r.user_id) if r.user_id else None,
                "user_email": r.user_email,
                "user_name": r.user_name,
                "user_role": r.user_role,
                "tenant_id": str(r.tenant_id) if r.tenant_id else None,
                "tenant_name": r.tenant_name,
                "ip_address": r.ip_address,
                "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
                "event_count": r.event_count,
            }
            for r in rows
        ]

    # ------------- Maintenance -------------

    async def purge_old(self, db: AsyncSession, retention_days: int | None = None) -> int:
        """Delete audit entries older than retention_days. Returns count deleted."""
        if retention_days is None:
            cfg = await self.get_config(db)
            retention_days = cfg.get("retention_days", 90)
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(retention_days))
        result = await db.execute(
            delete(AuditLog).where(AuditLog.created_at < cutoff)
        )
        await db.flush()
        count = result.rowcount or 0
        if count:
            logger.info(f"Audit purge: removed {count} rows older than {retention_days} days")
        return count


_audit_service: AuditService | None = None


def get_audit_service() -> AuditService:
    global _audit_service
    if _audit_service is None:
        _audit_service = AuditService()
    return _audit_service
