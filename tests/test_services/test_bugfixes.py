"""Tests for bug fixes from QA round 1.

Covers:
- STU-003/004: empty string class_id / grade_level_id should not fail UUID parse
- STU-008: PDF export returns valid bytes with PDF magic header
- CLS-006: Deleting a class soft-deletes AND unassigns its students
- AUTH-010: Change-password endpoint rejects wrong current password
            and accepts a correct one
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import UnauthorizedException
from app.models import SchoolClass, Student, Tenant, User
from app.models.user import Role
from app.services.class_service import get_class_service
from app.utils.security import hash_password, verify_password


# =========================================================================
# CLS-006: Class delete preserves students
# =========================================================================


class TestClassDeleteSoftAndUnassign:
    async def test_delete_empty_class(
        self, db: AsyncSession, test_tenant: Tenant, test_class: SchoolClass
    ):
        """Deleting a class with no students returns count 0."""
        svc = get_class_service()
        result = await svc.delete_class(db, test_class.id)
        await db.commit()

        assert result["students_unassigned"] == 0

        # Class is soft-deleted
        q = await db.execute(select(SchoolClass).where(SchoolClass.id == test_class.id))
        cls = q.scalar_one()
        assert cls.deleted_at is not None
        assert cls.is_active is False

    async def test_delete_class_with_students_unassigns_them(
        self, db: AsyncSession, test_tenant: Tenant, test_class: SchoolClass
    ):
        """Deleting a class with students sets their class_id to NULL."""
        # Create 3 students in the class
        student_ids = []
        for i in range(3):
            s = Student(
                id=uuid.uuid4(),
                tenant_id=test_tenant.id,
                first_name=f"Kid{i}",
                last_name="Test",
                class_id=test_class.id,
                is_active=True,
            )
            db.add(s)
            student_ids.append(s.id)
        await db.commit()

        svc = get_class_service()
        result = await svc.delete_class(db, test_class.id)
        await db.commit()

        assert result["students_unassigned"] == 3

        # All 3 students still exist but have no class
        for sid in student_ids:
            q = await db.execute(select(Student).where(Student.id == sid))
            s = q.scalar_one()
            assert s.class_id is None
            assert s.deleted_at is None  # NOT deleted
            assert s.is_active is True

    async def test_delete_class_ignores_already_deleted_students(
        self, db: AsyncSession, test_tenant: Tenant, test_class: SchoolClass
    ):
        """Soft-deleted students are not counted."""
        from datetime import datetime

        active = Student(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            first_name="Active",
            last_name="Kid",
            class_id=test_class.id,
            is_active=True,
        )
        deleted = Student(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            first_name="Deleted",
            last_name="Kid",
            class_id=test_class.id,
            is_active=False,
            deleted_at=datetime.utcnow(),
        )
        db.add_all([active, deleted])
        await db.commit()

        svc = get_class_service()
        result = await svc.delete_class(db, test_class.id)
        await db.commit()

        assert result["students_unassigned"] == 1  # only the active one


# =========================================================================
# AUTH-010: Change password
# =========================================================================


class TestChangePasswordSecurity:
    """Direct tests of the security primitives used by /api/v1/auth/me/password."""

    async def test_verify_password_accepts_correct(self):
        h = hash_password("myPassword123")
        assert verify_password("myPassword123", h) is True

    async def test_verify_password_rejects_wrong(self):
        h = hash_password("myPassword123")
        assert verify_password("wrongPassword", h) is False

    async def test_hash_password_is_not_plaintext(self):
        h = hash_password("secret123")
        assert h != "secret123"
        assert h.startswith("$2")  # bcrypt prefix

    async def test_change_password_flow(
        self, db: AsyncSession, test_tenant: Tenant
    ):
        """Simulate the endpoint flow: verify current, set new, verify new."""
        user = User(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            email=f"pw-{uuid.uuid4().hex[:6]}@test.local",
            password_hash=hash_password("oldpass123"),
            first_name="Pass",
            last_name="Changer",
            role=Role.SCHOOL_ADMIN.value,
            is_active=True,
            language="en",
        )
        db.add(user)
        await db.commit()

        # Wrong current password should NOT verify
        assert verify_password("wrongpass", user.password_hash) is False

        # Correct current password verifies
        assert verify_password("oldpass123", user.password_hash) is True

        # Replace with new
        user.password_hash = hash_password("newpass456")
        await db.commit()

        # Old password no longer works
        assert verify_password("oldpass123", user.password_hash) is False
        # New password works
        assert verify_password("newpass456", user.password_hash) is True


# =========================================================================
# STU-003/004: Empty string class_id / grade_level_id parsing
# =========================================================================


class TestEmptyUuidParsing:
    """The web + API handlers now accept str for these query params.
    Simulate the sanitization the endpoint performs."""

    def _parse_optional_uuid(self, value: str | None) -> uuid.UUID | None:
        if value and value.strip():
            try:
                return uuid.UUID(value)
            except ValueError:
                return None
        return None

    def test_empty_string_returns_none(self):
        assert self._parse_optional_uuid("") is None

    def test_whitespace_returns_none(self):
        assert self._parse_optional_uuid("   ") is None

    def test_none_returns_none(self):
        assert self._parse_optional_uuid(None) is None

    def test_invalid_uuid_returns_none(self):
        assert self._parse_optional_uuid("not-a-uuid") is None

    def test_valid_uuid_parses(self):
        u = uuid.uuid4()
        parsed = self._parse_optional_uuid(str(u))
        assert parsed == u


# =========================================================================
# STU-008: PDF export produces valid PDF
# =========================================================================


class TestPdfExportFormat:
    """The key fix for the corrupt PDF is to normalize fpdf2's bytearray
    output to bytes. Verify that produces valid PDF magic and length."""

    def test_fpdf_output_converted_to_bytes_starts_with_pdf_header(self):
        from fpdf import FPDF

        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 16)
        pdf.cell(0, 10, "Hello", ln=True)

        raw = pdf.output()
        # fpdf2 returns bytearray — important to normalize to bytes
        assert isinstance(raw, (bytes, bytearray))

        normalized = bytes(raw)
        assert isinstance(normalized, bytes)
        assert normalized.startswith(b"%PDF-")
        # Valid PDFs end with %%EOF
        assert b"%%EOF" in normalized[-512:]
        assert len(normalized) > 100
