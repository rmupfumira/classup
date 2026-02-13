"""Bulk CSV import service."""

import csv
import io
import logging
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BulkImportJob, SchoolClass, Student, User
from app.utils.tenant_context import get_current_user_id, get_tenant_id

logger = logging.getLogger(__name__)


# Field definitions for each import type
IMPORT_FIELDS = {
    "STUDENTS": {
        "first_name": {"label": "First Name", "required": True},
        "last_name": {"label": "Last Name", "required": True},
        "date_of_birth": {"label": "Date of Birth", "required": False},
        "gender": {"label": "Gender", "required": False},
        "age_group": {"label": "Age Group", "required": False},
        "grade_level": {"label": "Grade Level", "required": False},
        "class_name": {"label": "Class Name", "required": False},
        "medical_info": {"label": "Medical Info", "required": False},
        "allergies": {"label": "Allergies", "required": False},
        "parent_email": {"label": "Parent Email", "required": False},
        "parent_phone": {"label": "Parent Phone", "required": False},
        "emergency_contact_name": {"label": "Emergency Contact Name", "required": False},
        "emergency_contact_phone": {"label": "Emergency Contact Phone", "required": False},
    },
    "TEACHERS": {
        "first_name": {"label": "First Name", "required": True},
        "last_name": {"label": "Last Name", "required": True},
        "email": {"label": "Email", "required": True},
        "phone": {"label": "Phone", "required": False},
        "class_names": {"label": "Class Names (comma-separated)", "required": False},
    },
    "PARENTS": {
        "first_name": {"label": "First Name", "required": True},
        "last_name": {"label": "Last Name", "required": True},
        "email": {"label": "Email", "required": True},
        "phone": {"label": "Phone", "required": False},
        "student_name": {"label": "Student Name", "required": False},
        "relationship": {"label": "Relationship", "required": False},
    },
}


class ImportService:
    """Service for handling bulk CSV imports."""

    def get_available_fields(self, import_type: str) -> dict:
        """Get available fields for an import type."""
        return IMPORT_FIELDS.get(import_type, {})

    async def create_job(
        self,
        db: AsyncSession,
        file_name: str,
        import_type: str,
        csv_content: str,
    ) -> tuple[BulkImportJob, list[str], list[dict], int]:
        """Create an import job and return preview data."""
        tenant_id = get_tenant_id()
        user_id = get_current_user_id()

        # Parse CSV to get headers and preview
        reader = csv.DictReader(io.StringIO(csv_content))
        headers = reader.fieldnames or []

        # Get sample rows (up to 5)
        sample_rows = []
        total_rows = 0
        for i, row in enumerate(reader):
            total_rows += 1
            if i < 5:
                sample_rows.append(dict(row))

        # Create job record
        job = BulkImportJob(
            tenant_id=tenant_id,
            import_type=import_type,
            file_name=file_name,
            status="PENDING",
            total_rows=total_rows,
            created_by=user_id,
            # Store CSV content in column_mapping temporarily until processing
            column_mapping={"_csv_content": csv_content},
        )

        db.add(job)
        await db.commit()
        await db.refresh(job)

        return job, headers, sample_rows, total_rows

    async def get_job(
        self,
        db: AsyncSession,
        job_id: UUID,
    ) -> BulkImportJob | None:
        """Get an import job by ID."""
        tenant_id = get_tenant_id()

        result = await db.execute(
            select(BulkImportJob).where(
                BulkImportJob.id == job_id,
                BulkImportJob.tenant_id == tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def start_import(
        self,
        db: AsyncSession,
        job_id: UUID,
        column_mapping: dict[str, str | None],
    ) -> BulkImportJob:
        """Start processing an import job with the given column mapping."""
        job = await self.get_job(db, job_id)
        if not job:
            raise ValueError("Import job not found")

        if job.status != "PENDING":
            raise ValueError("Job has already been started")

        # Get CSV content and clear it from mapping
        csv_content = job.column_mapping.get("_csv_content", "")
        job.column_mapping = column_mapping
        job.status = "PROCESSING"
        await db.commit()

        # Process the import
        try:
            await self._process_import(db, job, csv_content, column_mapping)
        except Exception as e:
            logger.error(f"Import job {job_id} failed: {e}")
            job.status = "FAILED"
            job.errors = [{"row": 0, "field": None, "message": str(e)}]
            await db.commit()

        return job

    async def _process_import(
        self,
        db: AsyncSession,
        job: BulkImportJob,
        csv_content: str,
        column_mapping: dict[str, str | None],
    ):
        """Process the actual import."""
        reader = csv.DictReader(io.StringIO(csv_content))
        errors = []
        success_count = 0
        processed_count = 0

        # Invert mapping: system_field -> csv_column
        field_to_csv = {}
        for csv_col, sys_field in column_mapping.items():
            if sys_field:
                field_to_csv[sys_field] = csv_col

        for row_num, row in enumerate(reader, start=2):  # Start at 2 (header is row 1)
            processed_count += 1
            try:
                if job.import_type == "STUDENTS":
                    await self._import_student_row(db, job.tenant_id, row, field_to_csv)
                elif job.import_type == "TEACHERS":
                    await self._import_teacher_row(db, job.tenant_id, row, field_to_csv)
                elif job.import_type == "PARENTS":
                    await self._import_parent_row(db, job.tenant_id, row, field_to_csv)

                success_count += 1
            except Exception as e:
                errors.append({
                    "row": row_num,
                    "field": None,
                    "value": None,
                    "message": str(e),
                })

            # Update progress periodically
            if processed_count % 50 == 0:
                job.processed_rows = processed_count
                job.success_count = success_count
                job.error_count = len(errors)
                await db.commit()

        # Final update
        job.processed_rows = processed_count
        job.success_count = success_count
        job.error_count = len(errors)
        job.errors = errors
        job.status = "COMPLETED"
        job.completed_at = datetime.now(timezone.utc)
        await db.commit()

    async def _import_student_row(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        row: dict,
        field_to_csv: dict[str, str],
    ):
        """Import a single student row."""
        # Get values using mapping
        def get_val(field: str) -> str | None:
            csv_col = field_to_csv.get(field)
            if csv_col and csv_col in row:
                val = row[csv_col].strip()
                return val if val else None
            return None

        first_name = get_val("first_name")
        last_name = get_val("last_name")

        if not first_name or not last_name:
            raise ValueError("First name and last name are required")

        # Parse date of birth
        dob = None
        dob_str = get_val("date_of_birth")
        if dob_str:
            for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"]:
                try:
                    dob = datetime.strptime(dob_str, fmt).date()
                    break
                except ValueError:
                    continue
            if not dob:
                raise ValueError(f"Invalid date format: {dob_str}")

        # Find class by name
        class_id = None
        class_name = get_val("class_name")
        if class_name:
            result = await db.execute(
                select(SchoolClass).where(
                    SchoolClass.tenant_id == tenant_id,
                    SchoolClass.name == class_name,
                    SchoolClass.deleted_at.is_(None),
                )
            )
            school_class = result.scalar_one_or_none()
            if school_class:
                class_id = school_class.id

        # Build emergency contacts
        emergency_contacts = []
        ec_name = get_val("emergency_contact_name")
        ec_phone = get_val("emergency_contact_phone")
        if ec_name and ec_phone:
            emergency_contacts.append({
                "name": ec_name,
                "phone": ec_phone,
                "relationship": "Emergency Contact",
            })

        student = Student(
            tenant_id=tenant_id,
            first_name=first_name,
            last_name=last_name,
            date_of_birth=dob,
            gender=get_val("gender"),
            age_group=get_val("age_group"),
            grade_level=get_val("grade_level"),
            class_id=class_id,
            medical_info=get_val("medical_info"),
            allergies=get_val("allergies"),
            emergency_contacts=emergency_contacts,
            enrollment_date=date.today(),
        )

        db.add(student)
        await db.flush()

    async def _import_teacher_row(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        row: dict,
        field_to_csv: dict[str, str],
    ):
        """Import a single teacher row."""
        from passlib.context import CryptContext
        import secrets

        pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

        def get_val(field: str) -> str | None:
            csv_col = field_to_csv.get(field)
            if csv_col and csv_col in row:
                val = row[csv_col].strip()
                return val if val else None
            return None

        first_name = get_val("first_name")
        last_name = get_val("last_name")
        email = get_val("email")

        if not first_name or not last_name or not email:
            raise ValueError("First name, last name, and email are required")

        # Check if email already exists
        existing = await db.execute(
            select(User).where(
                User.tenant_id == tenant_id,
                User.email == email.lower(),
                User.deleted_at.is_(None),
            )
        )
        if existing.scalar_one_or_none():
            raise ValueError(f"User with email {email} already exists")

        # Generate temporary password
        temp_password = secrets.token_urlsafe(12)

        teacher = User(
            tenant_id=tenant_id,
            email=email.lower(),
            password_hash=pwd_context.hash(temp_password),
            first_name=first_name,
            last_name=last_name,
            phone=get_val("phone"),
            role="TEACHER",
        )

        db.add(teacher)
        await db.flush()

        # TODO: Send welcome email with temporary password

    async def _import_parent_row(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        row: dict,
        field_to_csv: dict[str, str],
    ):
        """Import a single parent row."""
        from passlib.context import CryptContext
        import secrets

        pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

        def get_val(field: str) -> str | None:
            csv_col = field_to_csv.get(field)
            if csv_col and csv_col in row:
                val = row[csv_col].strip()
                return val if val else None
            return None

        first_name = get_val("first_name")
        last_name = get_val("last_name")
        email = get_val("email")

        if not first_name or not last_name or not email:
            raise ValueError("First name, last name, and email are required")

        # Check if email already exists
        existing = await db.execute(
            select(User).where(
                User.tenant_id == tenant_id,
                User.email == email.lower(),
                User.deleted_at.is_(None),
            )
        )
        if existing.scalar_one_or_none():
            raise ValueError(f"User with email {email} already exists")

        # Generate temporary password
        temp_password = secrets.token_urlsafe(12)

        parent = User(
            tenant_id=tenant_id,
            email=email.lower(),
            password_hash=pwd_context.hash(temp_password),
            first_name=first_name,
            last_name=last_name,
            phone=get_val("phone"),
            role="PARENT",
        )

        db.add(parent)
        await db.flush()

        # TODO: Link to student if student_name provided

    async def list_jobs(
        self,
        db: AsyncSession,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[BulkImportJob], int]:
        """List import jobs for the current tenant."""
        from sqlalchemy import func

        tenant_id = get_tenant_id()

        query = select(BulkImportJob).where(BulkImportJob.tenant_id == tenant_id)

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_query)).scalar() or 0

        # Apply pagination
        query = query.order_by(BulkImportJob.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(query)
        jobs = list(result.scalars().all())

        return jobs, total


# Singleton instance
_import_service: ImportService | None = None


def get_import_service() -> ImportService:
    """Get the import service singleton."""
    global _import_service
    if _import_service is None:
        _import_service = ImportService()
    return _import_service
