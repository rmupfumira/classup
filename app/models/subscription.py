"""Platform subscription models for tenant billing via Paystack."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, BaseModel, TimestampMixin, SoftDeleteMixin


class SubscriptionStatus(str, Enum):
    """Status of a tenant subscription."""

    TRIALING = "TRIALING"
    ACTIVE = "ACTIVE"
    PAST_DUE = "PAST_DUE"
    CANCELLED = "CANCELLED"
    SUSPENDED = "SUSPENDED"


class PlatformInvoiceStatus(str, Enum):
    """Status of a platform invoice."""

    PENDING = "PENDING"
    PAID = "PAID"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"


class SubscriptionPlan(Base, TimestampMixin, SoftDeleteMixin):
    """A subscription plan that tenants can subscribe to."""

    __tablename__ = "subscription_plans"
    __table_args__ = (
        Index(
            "idx_subscription_plans_active",
            "is_active",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_monthly: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False
    )
    price_annually: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="ZAR"
    )
    max_students: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_staff: Mapped[int | None] = mapped_column(Integer, nullable=True)
    features: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    trial_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    paystack_plan_code: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )

    # Relationships
    subscriptions = relationship("TenantSubscription", back_populates="plan", lazy="selectin")


class TenantSubscription(Base, TimestampMixin):
    """A tenant's active subscription to a plan."""

    __tablename__ = "tenant_subscriptions"
    __table_args__ = (
        Index("idx_tenant_subscriptions_tenant", "tenant_id"),
        Index(
            "idx_tenant_subscriptions_status",
            "tenant_id",
            "status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subscription_plans.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=SubscriptionStatus.TRIALING.value
    )
    trial_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    trial_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    current_period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    current_period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Paystack fields
    paystack_customer_code: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    paystack_subscription_code: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    paystack_email_token: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    paystack_authorization_code: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )
    # Grace period tracking
    grace_period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    failed_payment_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )

    # Relationships
    tenant = relationship("Tenant", lazy="selectin")
    plan = relationship("SubscriptionPlan", back_populates="subscriptions", lazy="selectin")
    invoices = relationship("PlatformInvoice", back_populates="subscription", lazy="selectin")


class PlatformInvoice(Base, TimestampMixin):
    """Invoice from ClassUp to a tenant for their subscription."""

    __tablename__ = "platform_invoices"
    __table_args__ = (
        Index("idx_platform_invoices_tenant", "tenant_id"),
        Index("idx_platform_invoices_subscription", "subscription_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenant_subscriptions.id", ondelete="CASCADE"),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="ZAR"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=PlatformInvoiceStatus.PENDING.value
    )
    billing_period_start: Mapped[date] = mapped_column(Date, nullable=False)
    billing_period_end: Mapped[date] = mapped_column(Date, nullable=False)
    # Paystack fields
    paystack_reference: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )
    paystack_transaction_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    payment_method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    tenant = relationship("Tenant", lazy="selectin")
    subscription = relationship("TenantSubscription", back_populates="invoices", lazy="selectin")
