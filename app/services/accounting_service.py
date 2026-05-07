"""Accounting service — chart of accounts, banks, vendors, transactions, reports.

Single-entry accounting for small/medium schools. Each transaction row is
one money movement. P&L / cash position reports are derived by aggregating
these rows.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundException, ValidationException
from app.models import (
    AccountType,
    AccountingTransaction,
    BankAccount,
    BankAccountType,
    ChartAccount,
    TransactionType,
    Vendor,
)
from app.utils.tenant_context import get_current_user_id_or_none, get_tenant_id

logger = logging.getLogger(__name__)


# Code prefix conventions:
#   1xxx = Assets / banks
#   2xxx = Liabilities
#   4xxx = Income
#   5xxx = Expenses
DEFAULT_INCOME_ACCOUNTS: list[tuple[str, str, bool]] = [
    # (code, name, is_system)
    ("4000", "Tuition Fees", True),         # default target for billing payments
    ("4010", "Registration Fees", False),
    ("4020", "Uniform Sales", False),
    ("4030", "Donations", False),
    ("4040", "Fundraising", False),
    ("4050", "Government Grants", False),
    ("4900", "Other Income", False),
]
DEFAULT_EXPENSE_ACCOUNTS: list[tuple[str, str, bool]] = [
    ("5000", "Salaries & Wages", False),
    ("5010", "Rent", False),
    ("5020", "Utilities", False),
    ("5030", "Stationery & Supplies", False),
    ("5040", "Maintenance & Repairs", False),
    ("5050", "Transport", False),
    ("5060", "Insurance", False),
    ("5070", "Marketing", False),
    ("5080", "Bank Charges", False),
    ("5090", "Cleaning", False),
    ("5100", "Catering", False),
    ("5110", "Training & Development", False),
    ("5900", "Other Expenses", False),
]
DEFAULT_ASSET_ACCOUNTS: list[tuple[str, str, bool]] = [
    ("1000", "Cash on Hand", False),
]
DEFAULT_LIABILITY_ACCOUNTS: list[tuple[str, str, bool]] = [
    ("2000", "Loans Payable", False),
    ("2010", "Accrued Expenses", False),
]


# Code that the system considers the "tuition" / default-income account
SYSTEM_TUITION_CODE = "4000"


class AccountingService:
    """Operations for the school's basic accounting module."""

    # ============================================================
    # Seeding (called when a tenant is created)
    # ============================================================

    async def seed_defaults_for_tenant(
        self, db: AsyncSession, tenant_id: uuid.UUID
    ) -> None:
        """Idempotent: create the default chart of accounts + a default bank.

        Safe to call repeatedly — only inserts what's missing.
        """
        existing = await db.execute(
            select(func.count(ChartAccount.id)).where(
                ChartAccount.tenant_id == tenant_id,
                ChartAccount.deleted_at.is_(None),
            )
        )
        if (existing.scalar() or 0) > 0:
            logger.debug(f"Tenant {tenant_id} already has accounts — skipping seed")
            return

        order = 0
        for type_, defaults in (
            (AccountType.INCOME, DEFAULT_INCOME_ACCOUNTS),
            (AccountType.EXPENSE, DEFAULT_EXPENSE_ACCOUNTS),
            (AccountType.ASSET, DEFAULT_ASSET_ACCOUNTS),
            (AccountType.LIABILITY, DEFAULT_LIABILITY_ACCOUNTS),
        ):
            for code, name, is_system in defaults:
                db.add(ChartAccount(
                    tenant_id=tenant_id,
                    code=code,
                    name=name,
                    type=type_.value,
                    is_active=True,
                    is_system=is_system,
                    display_order=order,
                ))
                order += 1

        # Seed a default operating bank account if none exists
        bank_count = await db.execute(
            select(func.count(BankAccount.id)).where(
                BankAccount.tenant_id == tenant_id,
                BankAccount.deleted_at.is_(None),
            )
        )
        if (bank_count.scalar() or 0) == 0:
            db.add(BankAccount(
                tenant_id=tenant_id,
                name="Operating Account",
                account_type=BankAccountType.OPERATING.value,
                opening_balance=Decimal("0.00"),
                is_default=True,
                is_active=True,
            ))

        await db.flush()

    # ============================================================
    # Chart of accounts CRUD
    # ============================================================

    async def list_chart_accounts(
        self,
        db: AsyncSession,
        type_filter: str | None = None,
        active_only: bool = True,
    ) -> list[ChartAccount]:
        tenant_id = get_tenant_id()
        stmt = select(ChartAccount).where(
            ChartAccount.tenant_id == tenant_id,
            ChartAccount.deleted_at.is_(None),
        )
        if active_only:
            stmt = stmt.where(ChartAccount.is_active == True)  # noqa: E712
        if type_filter:
            stmt = stmt.where(ChartAccount.type == type_filter.upper())
        stmt = stmt.order_by(ChartAccount.type, ChartAccount.display_order, ChartAccount.code)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def get_chart_account(
        self, db: AsyncSession, account_id: uuid.UUID
    ) -> ChartAccount:
        tenant_id = get_tenant_id()
        result = await db.execute(
            select(ChartAccount).where(
                ChartAccount.id == account_id,
                ChartAccount.tenant_id == tenant_id,
                ChartAccount.deleted_at.is_(None),
            )
        )
        acct = result.scalar_one_or_none()
        if not acct:
            raise NotFoundException("Account")
        return acct

    async def get_account_by_code(
        self, db: AsyncSession, code: str
    ) -> ChartAccount | None:
        tenant_id = get_tenant_id()
        result = await db.execute(
            select(ChartAccount).where(
                ChartAccount.tenant_id == tenant_id,
                ChartAccount.code == code,
                ChartAccount.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def create_chart_account(
        self,
        db: AsyncSession,
        code: str,
        name: str,
        type_: str,
        description: str | None = None,
    ) -> ChartAccount:
        tenant_id = get_tenant_id()
        type_norm = type_.upper()
        if type_norm not in {a.value for a in AccountType}:
            raise ValidationException([{"field": "type", "message": "Invalid account type"}])
        if not code.strip():
            raise ValidationException([{"field": "code", "message": "Code is required"}])
        if not name.strip():
            raise ValidationException([{"field": "name", "message": "Name is required"}])

        existing = await self.get_account_by_code(db, code.strip())
        if existing:
            raise ValidationException([{"field": "code", "message": f"Account code {code} already exists"}])

        # Determine display_order — append to end of its type group
        max_order = await db.execute(
            select(func.max(ChartAccount.display_order)).where(
                ChartAccount.tenant_id == tenant_id,
                ChartAccount.type == type_norm,
            )
        )
        next_order = (max_order.scalar() or 0) + 1

        acct = ChartAccount(
            tenant_id=tenant_id,
            code=code.strip(),
            name=name.strip(),
            type=type_norm,
            description=description,
            is_active=True,
            display_order=next_order,
        )
        db.add(acct)
        await db.flush()
        await db.refresh(acct)
        return acct

    async def update_chart_account(
        self,
        db: AsyncSession,
        account_id: uuid.UUID,
        name: str | None = None,
        description: str | None = None,
        is_active: bool | None = None,
    ) -> ChartAccount:
        acct = await self.get_chart_account(db, account_id)
        if name is not None:
            if not name.strip():
                raise ValidationException([{"field": "name", "message": "Name cannot be empty"}])
            acct.name = name.strip()
        if description is not None:
            acct.description = description.strip() if description.strip() else None
        if is_active is not None:
            if not is_active and acct.is_system:
                raise ValidationException([
                    {"field": "is_active", "message": "Cannot deactivate a system account"}
                ])
            acct.is_active = is_active
        await db.flush()
        await db.refresh(acct)
        return acct

    async def delete_chart_account(
        self, db: AsyncSession, account_id: uuid.UUID
    ) -> None:
        acct = await self.get_chart_account(db, account_id)
        if acct.is_system:
            raise ValidationException([
                {"field": "id", "message": "Cannot delete a system account; deactivate instead"}
            ])
        # Block deletion if the account is referenced by transactions
        used = await db.execute(
            select(func.count(AccountingTransaction.id)).where(
                AccountingTransaction.account_id == account_id,
                AccountingTransaction.deleted_at.is_(None),
            )
        )
        if (used.scalar() or 0) > 0:
            raise ValidationException([
                {"field": "id", "message": "Account is in use by existing transactions; deactivate instead"}
            ])
        acct.deleted_at = datetime.utcnow()
        acct.is_active = False
        await db.flush()

    # ============================================================
    # Bank accounts CRUD
    # ============================================================

    async def list_bank_accounts(
        self, db: AsyncSession, active_only: bool = True
    ) -> list[BankAccount]:
        tenant_id = get_tenant_id()
        stmt = select(BankAccount).where(
            BankAccount.tenant_id == tenant_id,
            BankAccount.deleted_at.is_(None),
        )
        if active_only:
            stmt = stmt.where(BankAccount.is_active == True)  # noqa: E712
        stmt = stmt.order_by(desc(BankAccount.is_default), BankAccount.name)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def get_bank_account(
        self, db: AsyncSession, bank_id: uuid.UUID
    ) -> BankAccount:
        tenant_id = get_tenant_id()
        result = await db.execute(
            select(BankAccount).where(
                BankAccount.id == bank_id,
                BankAccount.tenant_id == tenant_id,
                BankAccount.deleted_at.is_(None),
            )
        )
        bank = result.scalar_one_or_none()
        if not bank:
            raise NotFoundException("Bank account")
        return bank

    async def get_default_bank_account(
        self, db: AsyncSession
    ) -> BankAccount | None:
        tenant_id = get_tenant_id()
        result = await db.execute(
            select(BankAccount).where(
                BankAccount.tenant_id == tenant_id,
                BankAccount.deleted_at.is_(None),
                BankAccount.is_active == True,  # noqa: E712
            ).order_by(desc(BankAccount.is_default), BankAccount.name)
        )
        return result.scalars().first()

    async def create_bank_account(
        self,
        db: AsyncSession,
        name: str,
        bank_name: str | None = None,
        account_number: str | None = None,
        branch_code: str | None = None,
        account_type: str = "OPERATING",
        currency: str = "ZAR",
        opening_balance: Decimal = Decimal("0"),
        opening_balance_date: date | None = None,
        is_default: bool = False,
        notes: str | None = None,
    ) -> BankAccount:
        tenant_id = get_tenant_id()
        if not name.strip():
            raise ValidationException([{"field": "name", "message": "Name is required"}])
        if account_type.upper() not in {a.value for a in BankAccountType}:
            raise ValidationException([{"field": "account_type", "message": "Invalid account type"}])

        # If marking as default, unset others
        if is_default:
            await self._unset_default_banks(db)

        bank = BankAccount(
            tenant_id=tenant_id,
            name=name.strip(),
            bank_name=bank_name.strip() if bank_name else None,
            account_number=account_number.strip() if account_number else None,
            branch_code=branch_code.strip() if branch_code else None,
            account_type=account_type.upper(),
            currency=(currency or "ZAR").strip().upper()[:3],
            opening_balance=opening_balance or Decimal("0"),
            opening_balance_date=opening_balance_date,
            is_default=is_default,
            is_active=True,
            notes=notes,
        )
        db.add(bank)
        await db.flush()
        await db.refresh(bank)
        return bank

    async def update_bank_account(
        self,
        db: AsyncSession,
        bank_id: uuid.UUID,
        **fields,
    ) -> BankAccount:
        bank = await self.get_bank_account(db, bank_id)

        if "is_default" in fields and fields["is_default"]:
            await self._unset_default_banks(db, except_id=bank_id)

        editable = {
            "name", "bank_name", "account_number", "branch_code", "account_type",
            "currency", "opening_balance", "opening_balance_date", "is_default",
            "is_active", "notes",
        }
        for k, v in fields.items():
            if k not in editable or v is None:
                continue
            if k == "name" and not str(v).strip():
                raise ValidationException([{"field": "name", "message": "Name cannot be empty"}])
            if k == "account_type" and v.upper() not in {a.value for a in BankAccountType}:
                raise ValidationException([{"field": "account_type", "message": "Invalid"}])
            if k == "currency":
                v = (v or "ZAR").strip().upper()[:3]
            elif k == "account_type":
                v = v.upper()
            setattr(bank, k, v)
        await db.flush()
        await db.refresh(bank)
        return bank

    async def delete_bank_account(
        self, db: AsyncSession, bank_id: uuid.UUID
    ) -> None:
        bank = await self.get_bank_account(db, bank_id)
        used = await db.execute(
            select(func.count(AccountingTransaction.id)).where(
                or_(
                    AccountingTransaction.bank_account_id == bank_id,
                    AccountingTransaction.transfer_to_bank_account_id == bank_id,
                ),
                AccountingTransaction.deleted_at.is_(None),
            )
        )
        if (used.scalar() or 0) > 0:
            raise ValidationException([
                {"field": "id", "message": "Bank account has transactions; deactivate instead"}
            ])
        bank.deleted_at = datetime.utcnow()
        bank.is_active = False
        await db.flush()

    async def _unset_default_banks(
        self, db: AsyncSession, except_id: uuid.UUID | None = None
    ) -> None:
        tenant_id = get_tenant_id()
        stmt = select(BankAccount).where(
            BankAccount.tenant_id == tenant_id,
            BankAccount.is_default == True,  # noqa: E712
        )
        if except_id is not None:
            stmt = stmt.where(BankAccount.id != except_id)
        for b in (await db.execute(stmt)).scalars().all():
            b.is_default = False

    async def get_bank_balance(
        self, db: AsyncSession, bank_id: uuid.UUID, as_of: date | None = None
    ) -> Decimal:
        """Calculate the running balance: opening + sum of incomes/transfers in
        − sum of expenses/transfers out, up to the as_of date inclusive."""
        bank = await self.get_bank_account(db, bank_id)
        balance = Decimal(bank.opening_balance or 0)

        cutoff_filter = (
            (AccountingTransaction.date <= as_of,) if as_of else ()
        )

        # Income / received amounts
        in_q = await db.execute(
            select(func.coalesce(func.sum(AccountingTransaction.amount), 0)).where(
                AccountingTransaction.bank_account_id == bank_id,
                AccountingTransaction.type == TransactionType.INCOME.value,
                AccountingTransaction.deleted_at.is_(None),
                *cutoff_filter,
            )
        )
        balance += Decimal(in_q.scalar() or 0)

        # Expense / paid out amounts
        out_q = await db.execute(
            select(func.coalesce(func.sum(AccountingTransaction.amount), 0)).where(
                AccountingTransaction.bank_account_id == bank_id,
                AccountingTransaction.type == TransactionType.EXPENSE.value,
                AccountingTransaction.deleted_at.is_(None),
                *cutoff_filter,
            )
        )
        balance -= Decimal(out_q.scalar() or 0)

        # Transfers OUT
        t_out_q = await db.execute(
            select(func.coalesce(func.sum(AccountingTransaction.amount), 0)).where(
                AccountingTransaction.bank_account_id == bank_id,
                AccountingTransaction.type == TransactionType.TRANSFER.value,
                AccountingTransaction.deleted_at.is_(None),
                *cutoff_filter,
            )
        )
        balance -= Decimal(t_out_q.scalar() or 0)

        # Transfers IN
        t_in_q = await db.execute(
            select(func.coalesce(func.sum(AccountingTransaction.amount), 0)).where(
                AccountingTransaction.transfer_to_bank_account_id == bank_id,
                AccountingTransaction.type == TransactionType.TRANSFER.value,
                AccountingTransaction.deleted_at.is_(None),
                *cutoff_filter,
            )
        )
        balance += Decimal(t_in_q.scalar() or 0)

        return balance

    # ============================================================
    # Vendors CRUD
    # ============================================================

    async def list_vendors(
        self, db: AsyncSession, active_only: bool = True, search: str | None = None
    ) -> list[Vendor]:
        tenant_id = get_tenant_id()
        stmt = select(Vendor).where(
            Vendor.tenant_id == tenant_id,
            Vendor.deleted_at.is_(None),
        )
        if active_only:
            stmt = stmt.where(Vendor.is_active == True)  # noqa: E712
        if search and search.strip():
            q = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(
                    Vendor.name.ilike(q),
                    Vendor.contact_person.ilike(q),
                    Vendor.email.ilike(q),
                )
            )
        stmt = stmt.order_by(Vendor.name)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def get_vendor(self, db: AsyncSession, vendor_id: uuid.UUID) -> Vendor:
        tenant_id = get_tenant_id()
        result = await db.execute(
            select(Vendor).where(
                Vendor.id == vendor_id,
                Vendor.tenant_id == tenant_id,
                Vendor.deleted_at.is_(None),
            )
        )
        v = result.scalar_one_or_none()
        if not v:
            raise NotFoundException("Vendor")
        return v

    async def create_vendor(
        self, db: AsyncSession, **fields
    ) -> Vendor:
        tenant_id = get_tenant_id()
        if not fields.get("name", "").strip():
            raise ValidationException([{"field": "name", "message": "Name is required"}])

        v = Vendor(
            tenant_id=tenant_id,
            name=fields["name"].strip(),
            contact_person=fields.get("contact_person"),
            email=fields.get("email"),
            phone=fields.get("phone"),
            address=fields.get("address"),
            vat_number=fields.get("vat_number"),
            banking_details=fields.get("banking_details"),
            notes=fields.get("notes"),
            is_active=True,
        )
        db.add(v)
        await db.flush()
        await db.refresh(v)
        return v

    async def update_vendor(
        self, db: AsyncSession, vendor_id: uuid.UUID, **fields
    ) -> Vendor:
        v = await self.get_vendor(db, vendor_id)
        editable = {
            "name", "contact_person", "email", "phone", "address",
            "vat_number", "banking_details", "notes", "is_active",
        }
        for k, val in fields.items():
            if k in editable and val is not None:
                if k == "name" and not str(val).strip():
                    raise ValidationException([{"field": "name", "message": "Name cannot be empty"}])
                setattr(v, k, val.strip() if isinstance(val, str) else val)
        await db.flush()
        await db.refresh(v)
        return v

    async def delete_vendor(self, db: AsyncSession, vendor_id: uuid.UUID) -> None:
        v = await self.get_vendor(db, vendor_id)
        v.deleted_at = datetime.utcnow()
        v.is_active = False
        await db.flush()

    # ============================================================
    # Transactions
    # ============================================================

    async def list_transactions(
        self,
        db: AsyncSession,
        type_filter: str | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        bank_account_id: uuid.UUID | None = None,
        account_id: uuid.UUID | None = None,
        vendor_id: uuid.UUID | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[AccountingTransaction], int]:
        tenant_id = get_tenant_id()
        stmt = select(AccountingTransaction).where(
            AccountingTransaction.tenant_id == tenant_id,
            AccountingTransaction.deleted_at.is_(None),
        )
        if type_filter:
            stmt = stmt.where(AccountingTransaction.type == type_filter.upper())
        if from_date:
            stmt = stmt.where(AccountingTransaction.date >= from_date)
        if to_date:
            stmt = stmt.where(AccountingTransaction.date <= to_date)
        if bank_account_id:
            stmt = stmt.where(
                or_(
                    AccountingTransaction.bank_account_id == bank_account_id,
                    AccountingTransaction.transfer_to_bank_account_id == bank_account_id,
                )
            )
        if account_id:
            stmt = stmt.where(AccountingTransaction.account_id == account_id)
        if vendor_id:
            stmt = stmt.where(AccountingTransaction.vendor_id == vendor_id)
        if search and search.strip():
            q = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(
                    AccountingTransaction.description.ilike(q),
                    AccountingTransaction.reference.ilike(q),
                )
            )

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await db.execute(count_stmt)).scalar() or 0

        stmt = stmt.order_by(desc(AccountingTransaction.date), desc(AccountingTransaction.created_at))
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        result = await db.execute(stmt)
        return list(result.scalars().all()), total

    async def get_transaction(
        self, db: AsyncSession, tx_id: uuid.UUID
    ) -> AccountingTransaction:
        tenant_id = get_tenant_id()
        result = await db.execute(
            select(AccountingTransaction).where(
                AccountingTransaction.id == tx_id,
                AccountingTransaction.tenant_id == tenant_id,
                AccountingTransaction.deleted_at.is_(None),
            )
        )
        tx = result.scalar_one_or_none()
        if not tx:
            raise NotFoundException("Transaction")
        return tx

    async def record_expense(
        self,
        db: AsyncSession,
        date_: date,
        amount: Decimal,
        account_id: uuid.UUID,
        bank_account_id: uuid.UUID,
        vendor_id: uuid.UUID | None = None,
        description: str | None = None,
        reference: str | None = None,
        vat_amount: Decimal | None = None,
        vat_rate: Decimal | None = None,
        receipt_file_id: uuid.UUID | None = None,
    ) -> AccountingTransaction:
        if amount <= 0:
            raise ValidationException([{"field": "amount", "message": "Amount must be > 0"}])
        # Validate account is EXPENSE
        acct = await self.get_chart_account(db, account_id)
        if acct.type != AccountType.EXPENSE.value:
            raise ValidationException([
                {"field": "account_id", "message": "Selected account must be an Expense account"}
            ])
        await self.get_bank_account(db, bank_account_id)

        tenant_id = get_tenant_id()
        tx = AccountingTransaction(
            tenant_id=tenant_id,
            date=date_,
            type=TransactionType.EXPENSE.value,
            amount=amount,
            account_id=account_id,
            bank_account_id=bank_account_id,
            vendor_id=vendor_id,
            description=description,
            reference=reference,
            vat_amount=vat_amount,
            vat_rate=vat_rate,
            receipt_file_id=receipt_file_id,
            created_by=get_current_user_id_or_none(),
        )
        db.add(tx)
        await db.flush()
        await db.refresh(tx)
        return tx

    async def record_income(
        self,
        db: AsyncSession,
        date_: date,
        amount: Decimal,
        account_id: uuid.UUID,
        bank_account_id: uuid.UUID,
        student_id: uuid.UUID | None = None,
        description: str | None = None,
        reference: str | None = None,
        vat_amount: Decimal | None = None,
        vat_rate: Decimal | None = None,
        receipt_file_id: uuid.UUID | None = None,
    ) -> AccountingTransaction:
        if amount <= 0:
            raise ValidationException([{"field": "amount", "message": "Amount must be > 0"}])
        acct = await self.get_chart_account(db, account_id)
        if acct.type != AccountType.INCOME.value:
            raise ValidationException([
                {"field": "account_id", "message": "Selected account must be an Income account"}
            ])
        await self.get_bank_account(db, bank_account_id)

        tenant_id = get_tenant_id()
        tx = AccountingTransaction(
            tenant_id=tenant_id,
            date=date_,
            type=TransactionType.INCOME.value,
            amount=amount,
            account_id=account_id,
            bank_account_id=bank_account_id,
            student_id=student_id,
            description=description,
            reference=reference,
            vat_amount=vat_amount,
            vat_rate=vat_rate,
            receipt_file_id=receipt_file_id,
            created_by=get_current_user_id_or_none(),
        )
        db.add(tx)
        await db.flush()
        await db.refresh(tx)
        return tx

    async def record_transfer(
        self,
        db: AsyncSession,
        date_: date,
        amount: Decimal,
        from_bank_id: uuid.UUID,
        to_bank_id: uuid.UUID,
        description: str | None = None,
        reference: str | None = None,
    ) -> AccountingTransaction:
        if amount <= 0:
            raise ValidationException([{"field": "amount", "message": "Amount must be > 0"}])
        if from_bank_id == to_bank_id:
            raise ValidationException([
                {"field": "to_bank_id", "message": "Source and destination must differ"}
            ])
        await self.get_bank_account(db, from_bank_id)
        await self.get_bank_account(db, to_bank_id)

        tenant_id = get_tenant_id()
        tx = AccountingTransaction(
            tenant_id=tenant_id,
            date=date_,
            type=TransactionType.TRANSFER.value,
            amount=amount,
            bank_account_id=from_bank_id,
            transfer_to_bank_account_id=to_bank_id,
            description=description,
            reference=reference,
            created_by=get_current_user_id_or_none(),
        )
        db.add(tx)
        await db.flush()
        await db.refresh(tx)
        return tx

    async def update_transaction(
        self,
        db: AsyncSession,
        tx_id: uuid.UUID,
        **fields,
    ) -> AccountingTransaction:
        tx = await self.get_transaction(db, tx_id)
        # Don't allow updating auto-linked billing payments — they're driven
        # by the source. Flag instead and tell the user.
        if tx.billing_payment_id:
            raise ValidationException([
                {"field": "id", "message": "This transaction is auto-linked from a billing payment; edit the source payment instead"}
            ])
        editable = {
            "date", "amount", "account_id", "bank_account_id", "vendor_id",
            "student_id", "description", "reference", "vat_amount", "vat_rate",
            "receipt_file_id",
        }
        for k, v in fields.items():
            if k in editable and v is not None:
                setattr(tx, k, v)
        await db.flush()
        await db.refresh(tx)
        return tx

    async def delete_transaction(
        self, db: AsyncSession, tx_id: uuid.UUID
    ) -> None:
        tx = await self.get_transaction(db, tx_id)
        if tx.billing_payment_id:
            raise ValidationException([
                {"field": "id", "message": "This transaction is auto-linked from a billing payment; delete the source payment to remove it"}
            ])
        tx.deleted_at = datetime.utcnow()
        await db.flush()

    # ============================================================
    # Auto-link from billing
    # ============================================================

    async def link_billing_payment(
        self,
        db: AsyncSession,
        billing_payment,
    ) -> AccountingTransaction | None:
        """Called when a BillingPayment is recorded. Creates a matching
        INCOME accounting transaction so the P&L picks it up.

        Idempotent — does nothing if a transaction already exists for the
        same billing_payment_id.
        """
        # Already linked?
        existing = await db.execute(
            select(AccountingTransaction).where(
                AccountingTransaction.billing_payment_id == billing_payment.id,
                AccountingTransaction.deleted_at.is_(None),
            )
        )
        if existing.scalar_one_or_none():
            return None

        # Find the system Tuition Fees account
        income_acct = await self.get_account_by_code(db, SYSTEM_TUITION_CODE)
        if not income_acct:
            # Tenant has no chart of accounts seeded — skip silently
            return None

        # Pick the default bank account, or any active one
        bank = await self.get_default_bank_account(db)
        if not bank:
            return None

        tenant_id = get_tenant_id()
        student_id = billing_payment.student_id if hasattr(billing_payment, "student_id") else None
        amount = billing_payment.amount

        tx = AccountingTransaction(
            tenant_id=tenant_id,
            date=billing_payment.payment_date,
            type=TransactionType.INCOME.value,
            amount=amount,
            account_id=income_acct.id,
            bank_account_id=bank.id,
            student_id=student_id,
            billing_payment_id=billing_payment.id,
            description=(
                f"Auto-linked from invoice payment "
                f"{billing_payment.reference_number or ''}"
            ).strip(),
            reference=billing_payment.reference_number,
            created_by=billing_payment.recorded_by if hasattr(billing_payment, "recorded_by") else None,
        )
        db.add(tx)
        await db.flush()
        await db.refresh(tx)
        return tx

    # ============================================================
    # Reports
    # ============================================================

    async def profit_and_loss(
        self,
        db: AsyncSession,
        from_date: date,
        to_date: date,
    ) -> dict[str, Any]:
        """Aggregate income vs expenses for the date range.

        Returns:
            {
                "from_date": ..., "to_date": ...,
                "income": [{account_id, code, name, total}, ...],
                "expense": [{account_id, code, name, total}, ...],
                "income_total": Decimal,
                "expense_total": Decimal,
                "net_profit": Decimal,
            }
        """
        tenant_id = get_tenant_id()

        async def _aggregate(tx_type: str) -> list[dict[str, Any]]:
            stmt = (
                select(
                    ChartAccount.id,
                    ChartAccount.code,
                    ChartAccount.name,
                    func.coalesce(func.sum(AccountingTransaction.amount), 0).label("total"),
                )
                .join(
                    AccountingTransaction,
                    AccountingTransaction.account_id == ChartAccount.id,
                )
                .where(
                    AccountingTransaction.tenant_id == tenant_id,
                    AccountingTransaction.deleted_at.is_(None),
                    AccountingTransaction.type == tx_type,
                    AccountingTransaction.date >= from_date,
                    AccountingTransaction.date <= to_date,
                )
                .group_by(ChartAccount.id, ChartAccount.code, ChartAccount.name)
                .order_by(ChartAccount.code)
            )
            rows = (await db.execute(stmt)).all()
            return [
                {
                    "account_id": str(r.id),
                    "code": r.code,
                    "name": r.name,
                    "total": Decimal(str(r.total or 0)),
                }
                for r in rows
            ]

        income = await _aggregate(TransactionType.INCOME.value)
        expense = await _aggregate(TransactionType.EXPENSE.value)

        income_total = sum((r["total"] for r in income), Decimal("0"))
        expense_total = sum((r["total"] for r in expense), Decimal("0"))

        return {
            "from_date": from_date.isoformat(),
            "to_date": to_date.isoformat(),
            "income": income,
            "expense": expense,
            "income_total": income_total,
            "expense_total": expense_total,
            "net_profit": income_total - expense_total,
        }

    async def expenses_by_category(
        self,
        db: AsyncSession,
        from_date: date,
        to_date: date,
    ) -> list[dict[str, Any]]:
        """Sum of expenses per category, sorted by total descending."""
        tenant_id = get_tenant_id()
        stmt = (
            select(
                ChartAccount.id,
                ChartAccount.code,
                ChartAccount.name,
                func.coalesce(func.sum(AccountingTransaction.amount), 0).label("total"),
                func.count(AccountingTransaction.id).label("count"),
            )
            .join(
                AccountingTransaction,
                AccountingTransaction.account_id == ChartAccount.id,
            )
            .where(
                AccountingTransaction.tenant_id == tenant_id,
                AccountingTransaction.deleted_at.is_(None),
                AccountingTransaction.type == TransactionType.EXPENSE.value,
                AccountingTransaction.date >= from_date,
                AccountingTransaction.date <= to_date,
            )
            .group_by(ChartAccount.id, ChartAccount.code, ChartAccount.name)
            .order_by(desc("total"))
        )
        rows = (await db.execute(stmt)).all()
        return [
            {
                "account_id": str(r.id),
                "code": r.code,
                "name": r.name,
                "total": Decimal(str(r.total or 0)),
                "count": int(r.count or 0),
            }
            for r in rows
        ]

    async def cash_position(self, db: AsyncSession, as_of: date | None = None) -> dict:
        """Cash on hand across all bank accounts."""
        as_of = as_of or date.today()
        banks = await self.list_bank_accounts(db, active_only=False)
        items = []
        total = Decimal("0")
        for b in banks:
            bal = await self.get_bank_balance(db, b.id, as_of=as_of)
            total += bal
            items.append({
                "bank_account_id": str(b.id),
                "name": b.name,
                "bank_name": b.bank_name,
                "account_type": b.account_type,
                "currency": b.currency,
                "is_default": b.is_default,
                "is_active": b.is_active,
                "balance": bal,
            })
        return {"as_of": as_of.isoformat(), "total": total, "banks": items}

    async def dashboard_summary(self, db: AsyncSession) -> dict:
        """A small summary for the accounting landing page: this-month income
        & expense, cash position, and counts.
        """
        today = date.today()
        first = today.replace(day=1)
        # Calculate month end
        if today.month == 12:
            next_month_first = today.replace(year=today.year + 1, month=1, day=1)
        else:
            next_month_first = today.replace(month=today.month + 1, day=1)
        month_end = next_month_first - timedelta(days=1)

        pl = await self.profit_and_loss(db, first, month_end)
        cash = await self.cash_position(db, today)
        return {
            "month_label": today.strftime("%B %Y"),
            "from_date": first.isoformat(),
            "to_date": month_end.isoformat(),
            "income_this_month": pl["income_total"],
            "expense_this_month": pl["expense_total"],
            "net_this_month": pl["net_profit"],
            "cash_total": cash["total"],
            "banks": cash["banks"],
        }


_accounting_service: AccountingService | None = None


def get_accounting_service() -> AccountingService:
    global _accounting_service
    if _accounting_service is None:
        _accounting_service = AccountingService()
    return _accounting_service
