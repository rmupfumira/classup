"""Service for generating + parsing the bulk-payment Excel template.

Use case: a school with 324 students is paid for 50 of those students by
a church in one lump. The admin wants to record all 50 payments in one
go without clicking through the Billing UI 50 times.

The download pre-populates every active student with their oldest unpaid
invoice_number and current balance so the admin only has to fill in the
amount being paid per row (blank rows are skipped on import). They can
also start from a blank template if they prefer.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Protection, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

logger = logging.getLogger(__name__)


# Column definitions mirror the ENROLLMENT template contract.
# (field_name, human_label, width, required)
PAYMENT_COLUMNS = [
    ("student_id", "Student ID (keep as-is)", 38, False),
    ("student_name", "Student (ref)", 28, False),
    ("invoice_number", "Invoice number", 20, False),
    ("current_balance", "Current balance (ref)", 18, False),
    ("amount", "Amount to pay *", 16, True),
    ("payment_method", "Payment method", 18, False),
    ("payment_date", "Payment date (YYYY-MM-DD) *", 22, True),
    ("reference_number", "Reference / receipt no.", 22, False),
    ("notes", "Notes", 30, False),
]

TEXT_FORMAT_FIELDS = {"invoice_number", "reference_number", "payment_date"}

HEADER_BG_COLOR = "1B3A6B"
HEADER_FONT_COLOR = "FFFFFF"
INSTRUCTIONS_FONT_COLOR = "6B7280"
REF_FILL_COLOR = "F3F4F6"  # Light grey — reference columns, read-only feel

PAYMENT_METHODS = ["CASH", "BANK_TRANSFER", "EFT", "CARD", "CHEQUE", "OTHER"]


class PaymentTemplateService:
    """Generate + parse the bulk-payment .xlsx template."""

    @staticmethod
    def generate_template(
        students_with_invoices: list[dict],
        default_payment_date: date | None = None,
        default_payment_method: str = "BANK_TRANSFER",
    ) -> bytes:
        """
        Args:
            students_with_invoices: list of {student_id, student_name,
              invoice_number, current_balance} — one entry per active
              student. invoice_number + current_balance are "" / 0 when
              the student has no unpaid invoices.
            default_payment_date: pre-filled into every row (admin
              usually overrides only the ones they actually pay).
            default_payment_method: dropdown default.

        Returns:
            Excel file bytes ready for streaming.
        """
        wb = Workbook()
        ws = wb.active
        ws.title = "Bulk Payments"

        # Styles
        header_font = Font(bold=True, color=HEADER_FONT_COLOR, size=11)
        header_fill = PatternFill(
            start_color=HEADER_BG_COLOR, end_color=HEADER_BG_COLOR, fill_type="solid",
        )
        header_alignment = Alignment(horizontal="center", vertical="center")
        thin_border = Border(
            left=Side(style="thin"), right=Side(style="thin"),
            top=Side(style="thin"), bottom=Side(style="thin"),
        )
        instruction_font = Font(italic=True, color=INSTRUCTIONS_FONT_COLOR, size=10)
        ref_fill = PatternFill(
            start_color=REF_FILL_COLOR, end_color=REF_FILL_COLOR, fill_type="solid",
        )
        locked = Protection(locked=True)
        unlocked = Protection(locked=False)

        data_rows = max(len(students_with_invoices) + 20, 50)
        last_row = 3 + data_rows
        # Columns the admin edits vs. reference columns
        editable_fields = {"amount", "payment_method", "payment_date", "reference_number", "notes"}
        text_cols = {
            idx for idx, (field_name, *_rest) in enumerate(PAYMENT_COLUMNS, 1)
            if field_name in TEXT_FORMAT_FIELDS
        }
        editable_col_idx = {
            idx for idx, (field_name, *_rest) in enumerate(PAYMENT_COLUMNS, 1)
            if field_name in editable_fields
        }

        # Row 1 — field name headers (machine-readable). Locked.
        for col_idx, (field_name, _, width, _) in enumerate(PAYMENT_COLUMNS, 1):
            cell = ws.cell(row=1, column=col_idx, value=field_name)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_alignment
            cell.border = thin_border
            cell.protection = locked
            ws.column_dimensions[get_column_letter(col_idx)].width = width

        # Row 2 — human labels. Locked.
        for col_idx, (_, label, _, _) in enumerate(PAYMENT_COLUMNS, 1):
            cell = ws.cell(row=2, column=col_idx, value=label)
            cell.font = instruction_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = thin_border
            cell.protection = locked

        ws.freeze_panes = "A3"

        # Pre-format every data cell — admins only edit the editable cols;
        # the reference cols (student_id, student_name, invoice_number,
        # current_balance) stay locked.
        iso_default = default_payment_date.isoformat() if default_payment_date else ""

        for row in range(3, last_row + 1):
            for col_idx, (field_name, *_rest) in enumerate(PAYMENT_COLUMNS, 1):
                cell = ws.cell(row=row, column=col_idx)
                if col_idx in editable_col_idx:
                    cell.protection = unlocked
                else:
                    cell.protection = locked
                    cell.fill = ref_fill
                if col_idx in text_cols:
                    cell.number_format = "@"
                # Pre-fill a sensible default date + method for rows that
                # have a student — saves the admin from typing the same
                # thing 50 times.
                if field_name == "payment_date" and iso_default:
                    cell.value = iso_default
                if field_name == "payment_method":
                    cell.value = default_payment_method

        # Fill in student reference rows
        for i, row_data in enumerate(students_with_invoices, start=3):
            ws.cell(row=i, column=1, value=str(row_data.get("student_id") or ""))
            ws.cell(row=i, column=2, value=row_data.get("student_name") or "")
            ws.cell(row=i, column=3, value=row_data.get("invoice_number") or "")
            balance = row_data.get("current_balance")
            if balance is not None and balance != "":
                try:
                    ws.cell(row=i, column=4, value=float(balance))
                except (TypeError, ValueError):
                    ws.cell(row=i, column=4, value="")

        # Payment method dropdown
        method_col_letter = get_column_letter(
            next(idx for idx, (f, *_r) in enumerate(PAYMENT_COLUMNS, 1) if f == "payment_method")
        )
        method_dv = DataValidation(
            type="list",
            formula1=f'"{",".join(PAYMENT_METHODS)}"',
            allow_blank=True,
        )
        method_dv.error = "Pick one: " + ", ".join(PAYMENT_METHODS)
        method_dv.errorTitle = "Invalid payment method"
        method_dv.prompt = "Payment method"
        method_dv.promptTitle = "Method"
        ws.add_data_validation(method_dv)
        method_dv.add(f"{method_col_letter}3:{method_col_letter}{last_row}")

        # Sheet protection — stop accidental overwrites of header rows
        # and reference columns without being a nuisance to power users.
        ws.protection.sheet = True
        ws.protection.formatCells = False
        ws.protection.sort = False
        ws.protection.autoFilter = False
        ws.protection.selectLockedCells = False
        ws.protection.selectUnlockedCells = False

        output = BytesIO()
        wb.save(output)
        output.seek(0)
        return output.getvalue()

    @staticmethod
    def parse_excel(file_bytes: bytes) -> tuple[list[dict], int]:
        """Parse an uploaded bulk-payment .xlsx.

        Skips rows without a student_id AND without an invoice_number —
        those are pre-populated reference rows the admin left blank (no
        payment this time). Returns (rows, total_non_empty_count).
        """
        wb = load_workbook(BytesIO(file_bytes), read_only=True, data_only=True)
        ws = wb.active

        field_names = [col[0] for col in PAYMENT_COLUMNS]
        rows: list[dict] = []

        for row_idx, row in enumerate(ws.iter_rows(min_row=3, values_only=True), start=3):
            if row is None:
                continue
            # Skip rows that are all blank
            if all(cell is None or (isinstance(cell, str) and cell.strip() == "") for cell in row):
                continue

            row_dict: dict = {}
            for col_idx, field_name in enumerate(field_names):
                value = row[col_idx] if col_idx < len(row) else None
                if isinstance(value, (date, datetime)):
                    value = value.strftime("%Y-%m-%d")
                elif value is None:
                    value = ""
                elif isinstance(value, float) and field_name == "invoice_number":
                    # Old templates may have written the invoice as a
                    # float; coerce to a plain digit string.
                    try:
                        value = str(int(value))
                    except (ValueError, OverflowError):
                        value = str(value).strip()
                elif isinstance(value, float) and field_name == "amount":
                    # Keep amount as Decimal for precision
                    value = Decimal(str(value))
                elif isinstance(value, Decimal) and field_name == "amount":
                    pass
                else:
                    value = str(value).strip()
                row_dict[field_name] = value

            # Skip rows where the admin left both student_id and
            # invoice_number blank — they're unused reference rows.
            if not row_dict.get("student_id") and not row_dict.get("invoice_number"):
                continue
            # Skip rows where no amount was entered — admin didn't
            # intend to pay for this student.
            amt_raw = row_dict.get("amount", "")
            if amt_raw == "" or amt_raw is None:
                continue
            # Coerce string-typed amount to Decimal for consistency
            if not isinstance(amt_raw, Decimal):
                try:
                    row_dict["amount"] = Decimal(str(amt_raw).replace(",", ""))
                except (InvalidOperation, ValueError, TypeError):
                    row_dict["amount"] = None  # service layer will refuse

            row_dict["_row_number"] = row_idx
            rows.append(row_dict)

        wb.close()
        return rows, len(rows)


def get_payment_template_service() -> PaymentTemplateService:
    return PaymentTemplateService()
