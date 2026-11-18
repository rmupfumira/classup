"""Service for generating and parsing Excel enrollment templates."""

import logging
from datetime import date, datetime
from io import BytesIO

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Protection, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

logger = logging.getLogger(__name__)

# Column definitions: (field_name, human_label, width, required)
# Added 2026-11-18: parent_first_name + parent_last_name so bulk-import
# rows can trigger parent invitations the same way the single-student
# Add form does. Phone columns are formatted as TEXT in generate_template
# so Excel doesn't scientific-notation "+263771234567" into "2.63E+11".
ENROLLMENT_COLUMNS = [
    ("first_name", "First Name *", 20, True),
    ("last_name", "Last Name *", 20, True),
    ("date_of_birth", "Date of Birth (YYYY-MM-DD)", 18, False),
    ("gender", "Gender", 12, False),
    ("class_name", "Class Name", 22, False),
    ("medical_info", "Medical Info", 25, False),
    ("allergies", "Allergies", 25, False),
    ("parent_first_name", "Parent First Name", 20, False),
    ("parent_last_name", "Parent Last Name", 20, False),
    ("parent_email", "Parent Email", 28, False),
    ("parent_phone", "Parent Phone", 20, False),
    ("emergency_contact_name", "Emergency Contact Name", 22, False),
    ("emergency_contact_phone", "Emergency Contact Phone", 22, False),
]

# Which columns must be formatted as text (prevents Excel's
# "2.63E+11" bug when a parent types +263771234567). Field names;
# resolved to column indices at generation time.
TEXT_FORMAT_FIELDS = {"parent_phone", "emergency_contact_phone", "date_of_birth"}

# Navy brand color
HEADER_BG_COLOR = "1B3A6B"
HEADER_FONT_COLOR = "FFFFFF"
INSTRUCTIONS_FONT_COLOR = "6B7280"


class EnrollmentTemplateService:
    """Generate downloadable Excel templates and parse uploaded ones."""

    @staticmethod
    def generate_template(
        class_names: list[str],
        prefill_class_name: str | None = None,
        data_rows: int = 500,
    ) -> bytes:
        """
        Generate an Excel enrollment template.

        Args:
            class_names: List of class names for dropdown validation.
            prefill_class_name: If provided, pre-fill the class_name column.
            data_rows: How many editable data rows to pre-format (3 .. 3+n).

        Returns:
            Excel file bytes ready for streaming response.

        2026-11-18 updates:
          - Phone / date columns pre-formatted as text (``@``) so Excel
            doesn't turn "+263771234567" into scientific notation.
          - Rows 1-2 locked (sheet protection, no password). Data cells
            3 .. 3+data_rows unlocked so the admin can still type in
            them. Prevents accidental type-over of the header keys.
        """
        wb = Workbook()
        ws = wb.active
        ws.title = "Student Enrollment"

        # Styles
        header_font = Font(bold=True, color=HEADER_FONT_COLOR, size=11)
        header_fill = PatternFill(
            start_color=HEADER_BG_COLOR,
            end_color=HEADER_BG_COLOR,
            fill_type="solid",
        )
        header_alignment = Alignment(horizontal="center", vertical="center")
        thin_border = Border(
            left=Side(style="thin"),
            right=Side(style="thin"),
            top=Side(style="thin"),
            bottom=Side(style="thin"),
        )
        instruction_font = Font(italic=True, color=INSTRUCTIONS_FONT_COLOR, size=10)
        locked = Protection(locked=True)
        unlocked = Protection(locked=False)

        # Resolve which column indices should be text-formatted.
        text_cols = {
            idx for idx, (field_name, _, _, _) in enumerate(ENROLLMENT_COLUMNS, 1)
            if field_name in TEXT_FORMAT_FIELDS
        }

        last_row = 3 + max(data_rows, 1)  # inclusive upper bound for data cells

        # Row 1: Field name headers (machine-readable keys). Locked.
        for col_idx, (field_name, _, width, _) in enumerate(ENROLLMENT_COLUMNS, 1):
            cell = ws.cell(row=1, column=col_idx, value=field_name)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_alignment
            cell.border = thin_border
            cell.protection = locked
            ws.column_dimensions[get_column_letter(col_idx)].width = width

        # Row 2: Human-readable labels / instructions. Locked.
        for col_idx, (_, label, _, _) in enumerate(ENROLLMENT_COLUMNS, 1):
            cell = ws.cell(row=2, column=col_idx, value=label)
            cell.font = instruction_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = thin_border
            cell.protection = locked

        # Freeze top 2 rows + sheet protection. Sheet-protection locks
        # every cell by default; the data cells below are then flipped
        # to unlocked so admins can still type in them.
        ws.freeze_panes = "A3"

        # Pre-format data rows (3 .. last_row). Every data cell is
        # unlocked; phone/date cells additionally get text number
        # format so Excel treats long digit strings as text.
        for row in range(3, last_row + 1):
            for col_idx, _ in enumerate(ENROLLMENT_COLUMNS, 1):
                cell = ws.cell(row=row, column=col_idx)
                cell.protection = unlocked
                if col_idx in text_cols:
                    cell.number_format = "@"

        # Data validation: Gender dropdown (column D)
        gender_col_letter = "D"
        gender_dv = DataValidation(
            type="list",
            formula1='"MALE,FEMALE,OTHER"',
            allow_blank=True,
        )
        gender_dv.error = "Please select MALE, FEMALE, or OTHER"
        gender_dv.errorTitle = "Invalid Gender"
        gender_dv.prompt = "Select gender"
        gender_dv.promptTitle = "Gender"
        ws.add_data_validation(gender_dv)
        gender_dv.add(f"{gender_col_letter}3:{gender_col_letter}{last_row}")

        # Data validation: Class name dropdown (column E)
        class_col_letter = "E"
        if class_names:
            # Excel data validation list has a ~255 char limit for formula1
            # Use a comma-separated list within quotes
            class_list_str = ",".join(class_names)
            if len(class_list_str) <= 250:
                class_dv = DataValidation(
                    type="list",
                    formula1=f'"{class_list_str}"',
                    allow_blank=True,
                )
            else:
                # Too many classes for inline list — write to a hidden sheet
                hidden_ws = wb.create_sheet("_classes")
                for i, name in enumerate(class_names, 1):
                    hidden_ws.cell(row=i, column=1, value=name)
                hidden_ws.sheet_state = "hidden"
                class_dv = DataValidation(
                    type="list",
                    formula1=f"=_classes!$A$1:$A${len(class_names)}",
                    allow_blank=True,
                )
            class_dv.error = "Please select a valid class name"
            class_dv.errorTitle = "Invalid Class"
            class_dv.prompt = "Select class"
            class_dv.promptTitle = "Class Name"
            ws.add_data_validation(class_dv)
            class_dv.add(f"{class_col_letter}3:{class_col_letter}{last_row}")

        # Pre-fill class name if specified
        if prefill_class_name:
            for row in range(3, min(103, last_row + 1)):  # Pre-fill 100 rows
                cell = ws.cell(row=row, column=5, value=prefill_class_name)
                cell.protection = unlocked

        # Lock the sheet. No password — the point is to stop accidents,
        # not a determined admin (who can "Review → Unprotect Sheet"
        # in Excel). Keep select/sort/filter on so power users can
        # still work with their data.
        ws.protection.sheet = True
        ws.protection.formatCells = False
        ws.protection.formatColumns = False
        ws.protection.formatRows = False
        ws.protection.insertColumns = False
        ws.protection.insertRows = False
        ws.protection.deleteColumns = False
        ws.protection.deleteRows = False
        ws.protection.selectLockedCells = False
        ws.protection.selectUnlockedCells = False
        ws.protection.sort = False
        ws.protection.autoFilter = False

        # Save to bytes
        output = BytesIO()
        wb.save(output)
        output.seek(0)
        return output.getvalue()

    @staticmethod
    def parse_excel(file_bytes: bytes) -> tuple[list[dict], int]:
        """
        Parse an uploaded Excel enrollment file.

        Args:
            file_bytes: Raw bytes of the uploaded .xlsx file.

        Returns:
            Tuple of (list of row dicts, total non-empty row count).
            Each dict has keys matching ENROLLMENT_COLUMNS field names.
        """
        wb = load_workbook(BytesIO(file_bytes), read_only=True, data_only=True)
        ws = wb.active

        # Field names in column order
        field_names = [col[0] for col in ENROLLMENT_COLUMNS]

        rows = []
        for row_idx, row in enumerate(ws.iter_rows(min_row=3, values_only=True), start=3):
            if row is None:
                continue

            # Check if row has actual student data (at least first_name or last_name)
            # Skip rows that only have pre-filled class names but no student info
            if all(cell is None or (isinstance(cell, str) and cell.strip() == "") for cell in row):
                continue

            # first_name is column A (index 0), last_name is column B (index 1)
            first_name_val = row[0] if len(row) > 0 else None
            last_name_val = row[1] if len(row) > 1 else None
            has_first = first_name_val is not None and (not isinstance(first_name_val, str) or first_name_val.strip() != "")
            has_last = last_name_val is not None and (not isinstance(last_name_val, str) or last_name_val.strip() != "")
            if not has_first and not has_last:
                continue

            row_dict = {}
            for col_idx, field_name in enumerate(field_names):
                value = row[col_idx] if col_idx < len(row) else None

                # Convert dates to string format
                if isinstance(value, (date, datetime)):
                    value = value.strftime("%Y-%m-%d")
                elif value is None:
                    value = ""
                elif isinstance(value, float) and field_name in TEXT_FORMAT_FIELDS:
                    # Excel default-numbers a phone like "+263771234567"
                    # into a float with scientific notation. New templates
                    # format phone columns as TEXT so this doesn't happen,
                    # but older templates in the wild still trip over it.
                    # str(value) would produce "2.63771234567e+11"; cast
                    # through int first so we get a clean digit string.
                    try:
                        value = str(int(value))
                    except (ValueError, OverflowError):
                        value = str(value).strip()
                else:
                    value = str(value).strip()

                row_dict[field_name] = value

            # Store the original Excel row number for error reporting
            row_dict["_row_number"] = row_idx
            rows.append(row_dict)

        wb.close()
        return rows, len(rows)


def get_enrollment_template_service() -> EnrollmentTemplateService:
    """Get an instance of the enrollment template service."""
    return EnrollmentTemplateService()
