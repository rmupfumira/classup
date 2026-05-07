"""Tests for the accounting service: seed, CRUD, transactions, billing
auto-link, and reports.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundException, ValidationException
from app.models import (
    AccountingTransaction,
    AccountType,
    BankAccount,
    ChartAccount,
    SchoolClass,
    Student,
    Tenant,
    TransactionType,
    User,
    Vendor,
)
from app.models.billing import BillingInvoice, BillingPayment, InvoiceStatus, PaymentMethod
from app.services.accounting_service import SYSTEM_TUITION_CODE, get_accounting_service


# =========================================================================
# Default seed
# =========================================================================


class TestSeedDefaults:
    async def test_seed_creates_chart_and_default_bank(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        svc = get_accounting_service()
        await svc.seed_defaults_for_tenant(db, test_tenant.id)
        await db.commit()

        accounts = await svc.list_chart_accounts(db, active_only=False)
        assert len(accounts) >= 18  # 7 income + 13 expense + 1 asset + 2 liability

        # System tuition account exists & is_system=True
        tuition = await svc.get_account_by_code(db, SYSTEM_TUITION_CODE)
        assert tuition is not None
        assert tuition.is_system is True
        assert tuition.type == AccountType.INCOME.value

        # Default bank exists
        banks = await svc.list_bank_accounts(db)
        assert len(banks) == 1
        assert banks[0].is_default is True
        assert banks[0].name == "Operating Account"

    async def test_seed_is_idempotent(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        svc = get_accounting_service()
        await svc.seed_defaults_for_tenant(db, test_tenant.id)
        await db.commit()
        first_count = len(await svc.list_chart_accounts(db, active_only=False))

        # Re-run
        await svc.seed_defaults_for_tenant(db, test_tenant.id)
        await db.commit()
        second_count = len(await svc.list_chart_accounts(db, active_only=False))

        assert first_count == second_count


# =========================================================================
# Chart of accounts CRUD
# =========================================================================


class TestChartAccounts:
    async def test_create_account(self, db: AsyncSession, test_tenant: Tenant):
        svc = get_accounting_service()
        acct = await svc.create_chart_account(
            db, code="5500", name="Software Subscriptions", type_="EXPENSE"
        )
        assert acct.id is not None
        assert acct.code == "5500"
        assert acct.is_system is False

    async def test_duplicate_code_rejected(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        svc = get_accounting_service()
        await svc.create_chart_account(db, code="5501", name="A", type_="EXPENSE")
        with pytest.raises(ValidationException):
            await svc.create_chart_account(db, code="5501", name="B", type_="EXPENSE")

    async def test_invalid_type_rejected(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        svc = get_accounting_service()
        with pytest.raises(ValidationException):
            await svc.create_chart_account(
                db, code="9999", name="Bad", type_="WHATEVER"
            )

    async def test_cannot_deactivate_system_account(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        svc = get_accounting_service()
        await svc.seed_defaults_for_tenant(db, test_tenant.id)
        await db.commit()
        tuition = await svc.get_account_by_code(db, SYSTEM_TUITION_CODE)
        with pytest.raises(ValidationException):
            await svc.update_chart_account(db, tuition.id, is_active=False)

    async def test_cannot_delete_system_account(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        svc = get_accounting_service()
        await svc.seed_defaults_for_tenant(db, test_tenant.id)
        await db.commit()
        tuition = await svc.get_account_by_code(db, SYSTEM_TUITION_CODE)
        with pytest.raises(ValidationException):
            await svc.delete_chart_account(db, tuition.id)

    async def test_cannot_delete_account_in_use(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        svc = get_accounting_service()
        await svc.seed_defaults_for_tenant(db, test_tenant.id)
        await db.commit()
        rent = await svc.get_account_by_code(db, "5010")
        bank = (await svc.list_bank_accounts(db))[0]
        await svc.record_expense(
            db,
            date_=date.today(),
            amount=Decimal("100"),
            account_id=rent.id,
            bank_account_id=bank.id,
        )
        await db.commit()
        with pytest.raises(ValidationException):
            await svc.delete_chart_account(db, rent.id)


# =========================================================================
# Bank accounts
# =========================================================================


class TestBankAccounts:
    async def test_create_bank_and_default_swap(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        svc = get_accounting_service()
        await svc.seed_defaults_for_tenant(db, test_tenant.id)
        await db.commit()

        # Adding a new default should unset the existing default
        new_bank = await svc.create_bank_account(
            db, name="Savings", bank_name="FNB",
            opening_balance=Decimal("1000"), is_default=True,
        )
        await db.commit()

        banks = await svc.list_bank_accounts(db)
        defaults = [b for b in banks if b.is_default]
        assert len(defaults) == 1
        assert defaults[0].id == new_bank.id

    async def test_bank_balance_tracks_transactions(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        svc = get_accounting_service()
        await svc.seed_defaults_for_tenant(db, test_tenant.id)
        await db.commit()
        bank = (await svc.list_bank_accounts(db))[0]
        # Patch opening balance
        bank.opening_balance = Decimal("500")
        await db.flush()

        income_acct = await svc.get_account_by_code(db, SYSTEM_TUITION_CODE)
        expense_acct = await svc.get_account_by_code(db, "5010")

        await svc.record_income(
            db, date_=date.today(), amount=Decimal("200"),
            account_id=income_acct.id, bank_account_id=bank.id,
        )
        await svc.record_expense(
            db, date_=date.today(), amount=Decimal("80"),
            account_id=expense_acct.id, bank_account_id=bank.id,
        )
        await db.flush()

        balance = await svc.get_bank_balance(db, bank.id)
        # 500 + 200 - 80 = 620
        assert balance == Decimal("620")


# =========================================================================
# Transactions
# =========================================================================


class TestTransactions:
    async def test_record_expense(self, db: AsyncSession, test_tenant: Tenant):
        svc = get_accounting_service()
        await svc.seed_defaults_for_tenant(db, test_tenant.id)
        await db.commit()
        bank = (await svc.list_bank_accounts(db))[0]
        rent = await svc.get_account_by_code(db, "5010")

        tx = await svc.record_expense(
            db,
            date_=date.today(),
            amount=Decimal("1500.00"),
            account_id=rent.id,
            bank_account_id=bank.id,
            description="May rent",
        )
        assert tx.type == TransactionType.EXPENSE.value
        assert tx.amount == Decimal("1500.00")

    async def test_record_expense_rejects_income_account(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        svc = get_accounting_service()
        await svc.seed_defaults_for_tenant(db, test_tenant.id)
        await db.commit()
        bank = (await svc.list_bank_accounts(db))[0]
        tuition = await svc.get_account_by_code(db, SYSTEM_TUITION_CODE)

        with pytest.raises(ValidationException):
            await svc.record_expense(
                db,
                date_=date.today(),
                amount=Decimal("100"),
                account_id=tuition.id,  # wrong type
                bank_account_id=bank.id,
            )

    async def test_record_expense_rejects_zero_amount(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        svc = get_accounting_service()
        await svc.seed_defaults_for_tenant(db, test_tenant.id)
        await db.commit()
        bank = (await svc.list_bank_accounts(db))[0]
        rent = await svc.get_account_by_code(db, "5010")
        with pytest.raises(ValidationException):
            await svc.record_expense(
                db, date_=date.today(), amount=Decimal("0"),
                account_id=rent.id, bank_account_id=bank.id,
            )

    async def test_transfer_requires_different_banks(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        svc = get_accounting_service()
        await svc.seed_defaults_for_tenant(db, test_tenant.id)
        await db.commit()
        bank = (await svc.list_bank_accounts(db))[0]

        with pytest.raises(ValidationException):
            await svc.record_transfer(
                db, date_=date.today(), amount=Decimal("100"),
                from_bank_id=bank.id, to_bank_id=bank.id,
            )

    async def test_delete_transaction(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        svc = get_accounting_service()
        await svc.seed_defaults_for_tenant(db, test_tenant.id)
        await db.commit()
        bank = (await svc.list_bank_accounts(db))[0]
        rent = await svc.get_account_by_code(db, "5010")
        tx = await svc.record_expense(
            db, date_=date.today(), amount=Decimal("50"),
            account_id=rent.id, bank_account_id=bank.id,
        )
        await db.flush()

        await svc.delete_transaction(db, tx.id)
        with pytest.raises(NotFoundException):
            await svc.get_transaction(db, tx.id)


# =========================================================================
# Vendors
# =========================================================================


class TestVendors:
    async def test_create_vendor(self, db: AsyncSession, test_tenant: Tenant):
        svc = get_accounting_service()
        v = await svc.create_vendor(db, name="Eskom", phone="0860037566")
        assert v.id is not None
        assert v.name == "Eskom"

    async def test_create_vendor_requires_name(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        svc = get_accounting_service()
        with pytest.raises(ValidationException):
            await svc.create_vendor(db, name="   ")


# =========================================================================
# Reports
# =========================================================================


class TestReports:
    async def test_profit_and_loss(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        svc = get_accounting_service()
        await svc.seed_defaults_for_tenant(db, test_tenant.id)
        await db.commit()
        bank = (await svc.list_bank_accounts(db))[0]
        income_acct = await svc.get_account_by_code(db, SYSTEM_TUITION_CODE)
        rent = await svc.get_account_by_code(db, "5010")
        utilities = await svc.get_account_by_code(db, "5020")

        today = date.today()
        await svc.record_income(
            db, date_=today, amount=Decimal("3500"),
            account_id=income_acct.id, bank_account_id=bank.id,
        )
        await svc.record_expense(
            db, date_=today, amount=Decimal("1000"),
            account_id=rent.id, bank_account_id=bank.id,
        )
        await svc.record_expense(
            db, date_=today, amount=Decimal("250"),
            account_id=utilities.id, bank_account_id=bank.id,
        )
        await db.flush()

        pl = await svc.profit_and_loss(
            db, from_date=today - timedelta(days=1), to_date=today + timedelta(days=1)
        )
        assert pl["income_total"] == Decimal("3500")
        assert pl["expense_total"] == Decimal("1250")
        assert pl["net_profit"] == Decimal("2250")
        assert len(pl["income"]) == 1
        assert len(pl["expense"]) == 2

    async def test_expenses_by_category_sorted_desc(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        svc = get_accounting_service()
        await svc.seed_defaults_for_tenant(db, test_tenant.id)
        await db.commit()
        bank = (await svc.list_bank_accounts(db))[0]
        rent = await svc.get_account_by_code(db, "5010")
        utilities = await svc.get_account_by_code(db, "5020")

        today = date.today()
        await svc.record_expense(
            db, date_=today, amount=Decimal("100"),
            account_id=utilities.id, bank_account_id=bank.id,
        )
        await svc.record_expense(
            db, date_=today, amount=Decimal("5000"),
            account_id=rent.id, bank_account_id=bank.id,
        )
        await db.flush()

        rows = await svc.expenses_by_category(
            db, from_date=today - timedelta(days=1), to_date=today + timedelta(days=1)
        )
        # Highest first
        assert rows[0]["code"] == "5010"
        assert rows[0]["total"] == Decimal("5000")
        assert rows[1]["code"] == "5020"

    async def test_cash_position_sums_balances(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        svc = get_accounting_service()
        await svc.seed_defaults_for_tenant(db, test_tenant.id)
        await db.commit()
        bank1 = (await svc.list_bank_accounts(db))[0]
        bank1.opening_balance = Decimal("500")
        bank2 = await svc.create_bank_account(
            db, name="Savings", opening_balance=Decimal("1000"),
        )
        await db.flush()

        cp = await svc.cash_position(db)
        assert cp["total"] == Decimal("1500")
        assert len(cp["banks"]) == 2

    async def test_dashboard_summary_returns_month_data(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        svc = get_accounting_service()
        await svc.seed_defaults_for_tenant(db, test_tenant.id)
        await db.commit()
        bank = (await svc.list_bank_accounts(db))[0]
        income_acct = await svc.get_account_by_code(db, SYSTEM_TUITION_CODE)
        await svc.record_income(
            db, date_=date.today(), amount=Decimal("1234.56"),
            account_id=income_acct.id, bank_account_id=bank.id,
        )
        await db.flush()

        summary = await svc.dashboard_summary(db)
        assert summary["income_this_month"] == Decimal("1234.56")
        assert summary["expense_this_month"] == Decimal("0")
        assert "month_label" in summary
        assert isinstance(summary["banks"], list)


# =========================================================================
# Auto-link from billing
# =========================================================================


@pytest_asyncio.fixture
async def billing_payment(
    db: AsyncSession,
    test_tenant: Tenant,
    test_admin: User,
    test_class: SchoolClass,
) -> BillingPayment:
    """Create a real BillingPayment so the FK on AccountingTransaction
    is satisfied in auto-link tests."""
    student = Student(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        first_name="Pat",
        last_name="Tester",
        class_id=test_class.id,
        is_active=True,
    )
    db.add(student)
    await db.flush()

    invoice = BillingInvoice(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        student_id=student.id,
        invoice_number=f"INV-TEST-{uuid.uuid4().hex[:6]}",
        due_date=date.today(),
        subtotal=Decimal("3500.00"),
        total_amount=Decimal("3500.00"),
        amount_paid=Decimal("0.00"),
        balance=Decimal("3500.00"),
        status=InvoiceStatus.SENT.value,
        created_by=test_admin.id,
    )
    db.add(invoice)
    await db.flush()

    payment = BillingPayment(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        invoice_id=invoice.id,
        student_id=student.id,
        amount=Decimal("3500.00"),
        payment_method=PaymentMethod.EFT.value,
        reference_number="PAY-001",
        payment_date=date.today(),
        recorded_by=test_admin.id,
    )
    db.add(payment)
    await db.commit()
    await db.refresh(payment)
    return payment


class TestAutoLink:
    async def test_link_creates_income_transaction(
        self,
        db: AsyncSession,
        test_tenant: Tenant,
        billing_payment: BillingPayment,
    ):
        svc = get_accounting_service()
        await svc.seed_defaults_for_tenant(db, test_tenant.id)
        await db.commit()

        tx = await svc.link_billing_payment(db, billing_payment)
        await db.flush()

        assert tx is not None
        assert tx.type == TransactionType.INCOME.value
        assert tx.amount == Decimal("3500.00")
        assert tx.billing_payment_id == billing_payment.id

        # Should hit the system Tuition Fees account
        tuition = await svc.get_account_by_code(db, SYSTEM_TUITION_CODE)
        assert tx.account_id == tuition.id

    async def test_link_is_idempotent(
        self,
        db: AsyncSession,
        test_tenant: Tenant,
        billing_payment: BillingPayment,
    ):
        svc = get_accounting_service()
        await svc.seed_defaults_for_tenant(db, test_tenant.id)
        await db.commit()

        first = await svc.link_billing_payment(db, billing_payment)
        await db.flush()
        second = await svc.link_billing_payment(db, billing_payment)
        assert first is not None
        assert second is None  # already linked

    async def test_linked_tx_cannot_be_deleted_directly(
        self,
        db: AsyncSession,
        test_tenant: Tenant,
        billing_payment: BillingPayment,
    ):
        svc = get_accounting_service()
        await svc.seed_defaults_for_tenant(db, test_tenant.id)
        await db.commit()
        tx = await svc.link_billing_payment(db, billing_payment)
        await db.flush()

        with pytest.raises(ValidationException):
            await svc.delete_transaction(db, tx.id)

    async def test_linked_tx_cannot_be_updated_directly(
        self,
        db: AsyncSession,
        test_tenant: Tenant,
        billing_payment: BillingPayment,
    ):
        svc = get_accounting_service()
        await svc.seed_defaults_for_tenant(db, test_tenant.id)
        await db.commit()
        tx = await svc.link_billing_payment(db, billing_payment)
        await db.flush()

        with pytest.raises(ValidationException):
            await svc.update_transaction(db, tx.id, amount=Decimal("999"))
