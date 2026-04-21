"""Tests for the plan-gated feature enforcement."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import FeatureLockedException
from app.models import Tenant
from app.models.user import Role
from app.utils.permissions import FEATURE_LABELS, require_feature
from app.utils.tenant_context import (
    _current_user_role,
    _tenant_id,
)


def _set_feature(tenant: Tenant, feature: str, enabled: bool) -> None:
    """Toggle one feature on the tenant.settings JSONB."""
    settings = dict(tenant.settings or {})
    features = dict(settings.get("features", {}))
    features[feature] = enabled
    settings["features"] = features
    tenant.settings = settings


class TestRequireFeature:
    async def test_passes_when_feature_enabled(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        _set_feature(test_tenant, "billing", True)
        await db.commit()

        dep = require_feature("billing")
        # Should complete without raising
        await dep(db)

    async def test_raises_when_feature_disabled(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        _set_feature(test_tenant, "billing", False)
        await db.commit()

        dep = require_feature("billing")
        with pytest.raises(FeatureLockedException) as exc_info:
            await dep(db)

        err = exc_info.value
        assert err.feature == "billing"
        assert err.status_code == 402
        assert "Billing" in err.message

    async def test_raises_when_feature_missing(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        """Missing feature key in settings counts as disabled."""
        settings = dict(test_tenant.settings or {})
        features = dict(settings.get("features", {}))
        features.pop("billing", None)
        settings["features"] = features
        test_tenant.settings = settings
        await db.commit()

        dep = require_feature("billing")
        with pytest.raises(FeatureLockedException):
            await dep(db)

    async def test_super_admin_bypasses(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        """Super admin can access any feature for support purposes."""
        _set_feature(test_tenant, "billing", False)
        await db.commit()

        token = _current_user_role.set(Role.SUPER_ADMIN.value)
        try:
            dep = require_feature("billing")
            # Should NOT raise, even though feature is off
            await dep(db)
        finally:
            _current_user_role.reset(token)

    async def test_no_tenant_context_passes(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        """Unauthenticated / no tenant — dep passes (auth middleware handles later)."""
        # Clear tenant context
        token = _tenant_id.set(None)
        try:
            dep = require_feature("billing")
            # Should NOT raise
            await dep(db)
        finally:
            _tenant_id.reset(token)


class TestFeatureLabels:
    def test_known_features_have_labels(self):
        for key in [
            "billing",
            "photo_sharing",
            "document_sharing",
            "timetable_management",
            "subject_management",
            "whatsapp_enabled",
        ]:
            assert key in FEATURE_LABELS
            assert FEATURE_LABELS[key]  # non-empty

    def test_exception_uses_default_label_for_unknown(self):
        """If the feature isn't in FEATURE_LABELS, it falls back to Title Case."""
        exc = FeatureLockedException("homework_tracking")
        assert "Homework Tracking" in exc.message
        assert exc.feature == "homework_tracking"
