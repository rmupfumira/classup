"""Attendance service for tracking student attendance."""

import logging
import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import func, select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.exceptions import ConflictException, ForbiddenException, NotFoundException, ValidationException
from app.models import AttendanceRecord, SchoolClass, Student, TeacherClass, Tenant, User
from app.models.attendance import AttendanceStatus
from app.models.user import Role
from app.models.student import ParentStudent
from app.schemas.attendance import (
    AttendanceRecordCreate,
    AttendanceRecordUpdate,
    AttendanceStatsResponse,
    BulkAttendanceCreate,
    BulkAttendanceResponse,
    StudentAttendanceSummary,
)
from app.utils.tenant_context import get_current_user_id, get_current_user_role, get_tenant_id

logger = logging.getLogger(__name__)


class AttendanceService:
    """Service for managing attendance records."""

    async def get_attendance_records(
        self,
        db: AsyncSession,
        class_id: uuid.UUID | None = None,
        student_id: uuid.UUID | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        status: AttendanceStatus | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[AttendanceRecord], int]:
        """Get attendance records with optional filters."""
        tenant_id = get_tenant_id()
        role = get_current_user_role()
        user_id = get_current_user_id()

        query = (
            select(AttendanceRecord)
            .where(AttendanceRecord.tenant_id == tenant_id)
            .options(
                selectinload(AttendanceRecord.student),
                selectinload(AttendanceRecord.school_class),
                selectinload(AttendanceRecord.recorded_by_user),
            )
        )

        # Teachers only see attendance for their assigned classes
        if role == Role.TEACHER.value:
            subquery = select(TeacherClass.class_id).where(TeacherClass.teacher_id == user_id)
            query = query.where(AttendanceRecord.class_id.in_(subquery))

        # Apply filters
        if class_id:
            query = query.where(AttendanceRecord.class_id == class_id)
        if student_id:
            query = query.where(AttendanceRecord.student_id == student_id)
        if date_from:
            query = query.where(AttendanceRecord.date >= date_from)
        if date_to:
            query = query.where(AttendanceRecord.date <= date_to)
        if status:
            query = query.where(AttendanceRecord.status == status.value)

        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_query)).scalar() or 0

        # Apply pagination and ordering
        query = query.order_by(AttendanceRecord.date.desc(), AttendanceRecord.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(query)
        records = list(result.scalars().unique().all())

        return records, total

    async def get_attendance_record(
        self,
        db: AsyncSession,
        record_id: uuid.UUID,
    ) -> AttendanceRecord:
        """Get a single attendance record by ID."""
        tenant_id = get_tenant_id()

        query = (
            select(AttendanceRecord)
            .where(
                AttendanceRecord.id == record_id,
                AttendanceRecord.tenant_id == tenant_id,
            )
            .options(
                selectinload(AttendanceRecord.student),
                selectinload(AttendanceRecord.school_class),
                selectinload(AttendanceRecord.recorded_by_user),
            )
        )

        result = await db.execute(query)
        record = result.scalar_one_or_none()

        if not record:
            raise NotFoundException("Attendance record")

        return record

    async def create_attendance_record(
        self,
        db: AsyncSession,
        data: AttendanceRecordCreate,
    ) -> AttendanceRecord:
        """Create a single attendance record."""
        tenant_id = get_tenant_id()
        user_id = get_current_user_id()

        # Verify student exists and belongs to tenant
        student = await self._get_student(db, data.student_id)

        # Verify class exists
        await self._get_class(db, data.class_id)

        # Check for existing record
        existing = await self._get_existing_record(db, data.student_id, data.date)
        if existing:
            raise ConflictException("Attendance record already exists for this student on this date")

        record = AttendanceRecord(
            tenant_id=tenant_id,
            student_id=data.student_id,
            class_id=data.class_id,
            date=data.date,
            status=data.status.value,
            check_in_time=data.check_in_time,
            check_out_time=data.check_out_time,
            notes=data.notes,
            recorded_by=user_id,
        )

        db.add(record)
        await db.flush()
        await db.refresh(record)

        # Notify parents of attendance status
        await self._notify_parents_attendance(
            db, student, record.status, record.date, record.notes
        )

        return record

    async def update_attendance_record(
        self,
        db: AsyncSession,
        record_id: uuid.UUID,
        data: AttendanceRecordUpdate,
    ) -> AttendanceRecord:
        """Update an attendance record."""
        record = await self.get_attendance_record(db, record_id)

        # Detect check-out transition (None → value)
        had_checkout = record.check_out_time is not None

        # Update fields
        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            if field == "status" and value:
                setattr(record, field, value.value)
            else:
                setattr(record, field, value)

        await db.flush()
        await db.refresh(record)

        # Notify parents if student was just checked out
        if not had_checkout and record.check_out_time is not None:
            try:
                student = await self._get_student(db, record.student_id)
                await self._notify_parents_pickup(
                    db, student, record.check_out_time, record.date
                )
            except Exception as e:
                logger.error(f"Failed to send pickup notification for student {record.student_id}: {e}")

        return record

    async def record_bulk_attendance(
        self,
        db: AsyncSession,
        data: BulkAttendanceCreate,
    ) -> BulkAttendanceResponse:
        """Record attendance for multiple students at once."""
        tenant_id = get_tenant_id()
        user_id = get_current_user_id()

        # Verify class exists
        await self._get_class(db, data.class_id)

        success_count = 0
        error_count = 0
        errors = []
        notify_student_ids = []  # Students to notify parents

        for record_data in data.records:
            try:
                # Check for existing record
                existing = await self._get_existing_record(db, record_data.student_id, data.date)

                if existing:
                    # Update existing record
                    existing.status = record_data.status.value
                    existing.check_in_time = record_data.check_in_time
                    existing.notes = record_data.notes
                    existing.recorded_by = user_id
                else:
                    # Create new record
                    record = AttendanceRecord(
                        tenant_id=tenant_id,
                        student_id=record_data.student_id,
                        class_id=data.class_id,
                        date=data.date,
                        status=record_data.status.value,
                        check_in_time=record_data.check_in_time,
                        notes=record_data.notes,
                        recorded_by=user_id,
                    )
                    db.add(record)

                success_count += 1

                # Track students for parent notification
                notify_student_ids.append(
                    (record_data.student_id, record_data.status.value, record_data.notes)
                )

            except Exception as e:
                error_count += 1
                errors.append({
                    "student_id": str(record_data.student_id),
                    "error": str(e),
                })

        await db.flush()

        # Send notifications to parents for all students
        for student_id, status, notes in notify_student_ids:
            try:
                student = await self._get_student(db, student_id)
                await self._notify_parents_attendance(db, student, status, data.date, notes)
            except Exception as e:
                logger.error(f"Failed to notify parents for student {student_id}: {e}")

        return BulkAttendanceResponse(
            success_count=success_count,
            error_count=error_count,
            errors=errors,
        )

    async def get_class_attendance_for_date(
        self,
        db: AsyncSession,
        class_id: uuid.UUID,
        target_date: date,
    ) -> dict:
        """Get attendance data for a class on a specific date."""
        tenant_id = get_tenant_id()

        # Get the class
        school_class = await self._get_class(db, class_id)

        # Get all students in the class
        students_query = (
            select(Student)
            .where(
                Student.tenant_id == tenant_id,
                Student.class_id == class_id,
                Student.deleted_at.is_(None),
                Student.is_active == True,
            )
            .order_by(Student.first_name, Student.last_name)
        )
        students_result = await db.execute(students_query)
        students = list(students_result.scalars().all())

        # Get existing attendance records for this date
        records_query = (
            select(AttendanceRecord)
            .where(
                AttendanceRecord.tenant_id == tenant_id,
                AttendanceRecord.class_id == class_id,
                AttendanceRecord.date == target_date,
            )
        )
        records_result = await db.execute(records_query)
        existing_records = {r.student_id: r for r in records_result.scalars().all()}

        # Build student list with attendance status
        student_data = []
        present_count = 0
        absent_count = 0
        late_count = 0
        excused_count = 0

        for student in students:
            record = existing_records.get(student.id)
            status = record.status if record else None
            check_in_time = record.check_in_time if record else None
            check_out_time = record.check_out_time if record else None
            notes = record.notes if record else None

            student_data.append({
                "student_id": str(student.id),
                "student_name": f"{student.first_name} {student.last_name}",
                "photo_path": student.photo_path,
                "status": status,
                "check_in_time": check_in_time.isoformat() if check_in_time else None,
                "check_out_time": check_out_time.isoformat() if check_out_time else None,
                "notes": notes,
                "record_id": str(record.id) if record else None,
            })

            if status == AttendanceStatus.PRESENT.value:
                present_count += 1
            elif status == AttendanceStatus.ABSENT.value:
                absent_count += 1
            elif status == AttendanceStatus.LATE.value:
                late_count += 1
            elif status == AttendanceStatus.EXCUSED.value:
                excused_count += 1

        total_students = len(students)
        attendance_rate = (present_count + late_count) / total_students * 100 if total_students > 0 else 0

        return {
            "class_id": str(class_id),
            "class_name": school_class.name,
            "date": target_date.isoformat(),
            "students": student_data,
            "stats": {
                "total_students": total_students,
                "present_count": present_count,
                "absent_count": absent_count,
                "late_count": late_count,
                "excused_count": excused_count,
                "attendance_rate": round(attendance_rate, 1),
            },
        }

    async def get_student_attendance_history(
        self,
        db: AsyncSession,
        student_id: uuid.UUID,
        date_from: date | None = None,
        date_to: date | None = None,
        page: int = 1,
        page_size: int = 30,
    ) -> tuple[list[AttendanceRecord], int, StudentAttendanceSummary]:
        """Get attendance history for a specific student."""
        tenant_id = get_tenant_id()

        # Verify student exists
        student = await self._get_student(db, student_id)

        # Default to last 30 days if no date range specified
        if not date_from:
            date_from = date.today() - timedelta(days=30)
        if not date_to:
            date_to = date.today()

        # Get records
        records, total = await self.get_attendance_records(
            db,
            student_id=student_id,
            date_from=date_from,
            date_to=date_to,
            page=page,
            page_size=page_size,
        )

        # Calculate summary stats
        stats_query = (
            select(
                func.count().label("total"),
                func.count().filter(AttendanceRecord.status == AttendanceStatus.PRESENT.value).label("present"),
                func.count().filter(AttendanceRecord.status == AttendanceStatus.ABSENT.value).label("absent"),
                func.count().filter(AttendanceRecord.status == AttendanceStatus.LATE.value).label("late"),
                func.count().filter(AttendanceRecord.status == AttendanceStatus.EXCUSED.value).label("excused"),
            )
            .where(
                AttendanceRecord.tenant_id == tenant_id,
                AttendanceRecord.student_id == student_id,
                AttendanceRecord.date >= date_from,
                AttendanceRecord.date <= date_to,
            )
        )
        stats_result = await db.execute(stats_query)
        stats = stats_result.one()

        total_days = stats.total or 0
        present_days = stats.present or 0
        absent_days = stats.absent or 0
        late_days = stats.late or 0
        excused_days = stats.excused or 0

        attendance_rate = (present_days + late_days) / total_days * 100 if total_days > 0 else 0

        summary = StudentAttendanceSummary(
            student_id=student_id,
            student_name=f"{student.first_name} {student.last_name}",
            total_days=total_days,
            present_days=present_days,
            absent_days=absent_days,
            late_days=late_days,
            excused_days=excused_days,
            attendance_rate=round(attendance_rate, 1),
        )

        return records, total, summary

    async def get_attendance_stats(
        self,
        db: AsyncSession,
        class_id: uuid.UUID | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> AttendanceStatsResponse:
        """Get overall attendance statistics."""
        tenant_id = get_tenant_id()

        # Default to current month if no date range
        if not date_from:
            today = date.today()
            date_from = today.replace(day=1)
        if not date_to:
            date_to = date.today()

        query = select(
            func.count(func.distinct(AttendanceRecord.student_id)).label("total_students"),
            func.count().filter(AttendanceRecord.status == AttendanceStatus.PRESENT.value).label("present"),
            func.count().filter(AttendanceRecord.status == AttendanceStatus.ABSENT.value).label("absent"),
            func.count().filter(AttendanceRecord.status == AttendanceStatus.LATE.value).label("late"),
            func.count().filter(AttendanceRecord.status == AttendanceStatus.EXCUSED.value).label("excused"),
        ).where(
            AttendanceRecord.tenant_id == tenant_id,
            AttendanceRecord.date >= date_from,
            AttendanceRecord.date <= date_to,
        )

        if class_id:
            query = query.where(AttendanceRecord.class_id == class_id)

        result = await db.execute(query)
        stats = result.one()

        total_records = (stats.present or 0) + (stats.absent or 0) + (stats.late or 0) + (stats.excused or 0)
        present_count = (stats.present or 0) + (stats.late or 0)
        attendance_rate = present_count / total_records * 100 if total_records > 0 else 0

        return AttendanceStatsResponse(
            total_students=stats.total_students or 0,
            present_count=stats.present or 0,
            absent_count=stats.absent or 0,
            late_count=stats.late or 0,
            excused_count=stats.excused or 0,
            attendance_rate=round(attendance_rate, 1),
        )

    async def report_absence_by_parent(
        self,
        db: AsyncSession,
        student_id: uuid.UUID,
        absence_date: date,
        reason: str,
    ) -> AttendanceRecord:
        """Allow a parent to report their child's absence as EXCUSED."""
        tenant_id = get_tenant_id()
        user_id = get_current_user_id()

        # Verify parent owns the student
        ps_query = select(ParentStudent).where(
            ParentStudent.parent_id == user_id,
            ParentStudent.student_id == student_id,
        )
        result = await db.execute(ps_query)
        if not result.scalar_one_or_none():
            raise ForbiddenException("You can only report absence for your own children")

        # Date must be today or in the future
        if absence_date < date.today():
            raise ValidationException("Cannot report absence for a past date")

        # Get student
        student = await self._get_student(db, student_id)

        if not student.class_id:
            raise ValidationException("Student is not assigned to a class")

        # Check for existing record
        existing = await self._get_existing_record(db, student_id, absence_date)
        if existing:
            if existing.status == AttendanceStatus.EXCUSED.value:
                # Update notes on existing EXCUSED record
                existing.notes = f"Reported by parent: {reason}"
                await db.flush()
                await db.refresh(existing)
                return existing
            else:
                raise ConflictException(
                    "Attendance has already been recorded by the teacher for this date"
                )

        # Create EXCUSED record
        record = AttendanceRecord(
            tenant_id=tenant_id,
            student_id=student_id,
            class_id=student.class_id,
            date=absence_date,
            status=AttendanceStatus.EXCUSED.value,
            notes=f"Reported by parent: {reason}",
            recorded_by=user_id,
        )
        db.add(record)
        await db.flush()
        await db.refresh(record)

        # Notify staff
        try:
            await self._notify_staff_absence_reported(db, student, absence_date, reason)
        except Exception as e:
            logger.error(f"Failed to notify staff of absence report for student {student_id}: {e}")

        return record

    async def _notify_staff_absence_reported(
        self,
        db: AsyncSession,
        student: Student,
        absence_date: date,
        reason: str,
    ) -> None:
        """Notify teachers and admins when a parent reports an absence."""
        from app.services.email_service import get_email_service
        from app.services.notification_service import get_notification_service

        settings = get_settings()
        tenant = await db.get(Tenant, student.tenant_id)
        tenant_name = tenant.name if tenant else settings.app_name
        student_name = student.full_name
        date_str = absence_date.strftime("%B %d, %Y")

        email_service = get_email_service()
        notification_service = get_notification_service()

        title = f"Absence Reported: {student_name}"
        body = f"A parent has reported {student_name} absent for {date_str}. Reason: {reason}"

        # Notify class teachers
        if student.class_id:
            teacher_query = select(TeacherClass).where(
                TeacherClass.class_id == student.class_id,
            )
            result = await db.execute(teacher_query)
            teacher_classes = result.scalars().all()

            teacher_ids = []
            for tc in teacher_classes:
                teacher = await db.get(User, tc.teacher_id)
                if not teacher or not teacher.is_active:
                    continue
                teacher_ids.append(teacher.id)
                try:
                    await email_service.send_teacher_notification(
                        to=teacher.email,
                        teacher_name=teacher.first_name,
                        notification_type="ABSENCE_REPORTED",
                        title=title,
                        body=body,
                        tenant_name=tenant_name,
                    )
                except Exception as e:
                    logger.error(f"Failed to email absence report to teacher {teacher.email}: {e}")

            if teacher_ids:
                try:
                    await notification_service.create_bulk_notifications(
                        db=db,
                        user_ids=teacher_ids,
                        title=title,
                        body=body,
                        notification_type="ABSENCE_REPORTED",
                    )
                except Exception as e:
                    logger.error(f"Failed to create absence report notifications for teachers: {e}")

        # Notify admins
        try:
            await email_service.notify_admins(
                db=db,
                tenant_id=student.tenant_id,
                notification_type="ABSENCE_REPORTED",
                title=title,
                body=body,
            )
        except Exception as e:
            logger.error(f"Failed to notify admins of absence report: {e}")

    async def _notify_parents_attendance(
        self,
        db: AsyncSession,
        student: Student,
        status: str,
        attendance_date: date,
        notes: str | None = None,
    ) -> None:
        """Send email and in-app notifications to parents for attendance updates."""
        from app.services.email_service import get_email_service
        from app.services.notification_service import get_notification_service

        if not student.parent_students:
            logger.info(f"No parents linked to student {student.id} — skipping attendance alert")
            return

        settings = get_settings()
        tenant = await db.get(Tenant, student.tenant_id)
        tenant_name = tenant.name if tenant else settings.app_name
        student_name = student.full_name
        date_str = attendance_date.strftime("%B %d, %Y")

        email_service = get_email_service()
        notification_service = get_notification_service()

        parent_ids = []
        for ps in student.parent_students:
            parent = ps.parent
            if not parent or not parent.is_active:
                continue
            parent_ids.append(parent.id)
            try:
                await email_service.send_attendance_alert(
                    to=parent.email,
                    parent_name=parent.first_name,
                    student_name=student_name,
                    status=status,
                    date=date_str,
                    tenant_name=tenant_name,
                    notes=notes,
                )
            except Exception as e:
                logger.error(
                    f"Failed to email attendance alert to {parent.email} "
                    f"for student {student.id}: {e}"
                )

        if parent_ids:
            try:
                await notification_service.notify_attendance_marked(
                    db=db,
                    parent_ids=parent_ids,
                    student_name=student_name,
                    status=status,
                )
            except Exception as e:
                logger.error(
                    f"Failed to create attendance notifications for student {student.id}: {e}"
                )

    async def _notify_parents_pickup(
        self,
        db: AsyncSession,
        student: Student,
        checkout_time: datetime,
        attendance_date: date,
    ) -> None:
        """Send email and in-app notifications to parents when a student is checked out."""
        from app.services.email_service import get_email_service
        from app.services.notification_service import get_notification_service

        if not student.parent_students:
            logger.info(f"No parents linked to student {student.id} — skipping pickup alert")
            return

        settings = get_settings()
        tenant = await db.get(Tenant, student.tenant_id)
        tenant_name = tenant.name if tenant else settings.app_name
        student_name = student.full_name
        date_str = attendance_date.strftime("%B %d, %Y")
        time_str = checkout_time.strftime("%H:%M")

        email_service = get_email_service()
        notification_service = get_notification_service()

        parent_ids = []
        for ps in student.parent_students:
            parent = ps.parent
            if not parent or not parent.is_active:
                continue
            parent_ids.append(parent.id)
            try:
                await email_service.send_pickup_alert(
                    to=parent.email,
                    parent_name=parent.first_name,
                    student_name=student_name,
                    checkout_time=time_str,
                    date=date_str,
                    tenant_name=tenant_name,
                )
            except Exception as e:
                logger.error(
                    f"Failed to email pickup alert to {parent.email} "
                    f"for student {student.id}: {e}"
                )

        if parent_ids:
            try:
                await notification_service.notify_pickup(
                    db=db,
                    parent_ids=parent_ids,
                    student_name=student_name,
                    checkout_time=time_str,
                )
            except Exception as e:
                logger.error(
                    f"Failed to create pickup notifications for student {student.id}: {e}"
                )

    async def _get_student(self, db: AsyncSession, student_id: uuid.UUID) -> Student:
        """Get and verify a student exists."""
        tenant_id = get_tenant_id()

        query = select(Student).where(
            Student.id == student_id,
            Student.tenant_id == tenant_id,
            Student.deleted_at.is_(None),
        )
        result = await db.execute(query)
        student = result.scalar_one_or_none()

        if not student:
            raise NotFoundException("Student")

        return student

    async def _get_class(self, db: AsyncSession, class_id: uuid.UUID) -> SchoolClass:
        """Get and verify a class exists."""
        tenant_id = get_tenant_id()

        query = select(SchoolClass).where(
            SchoolClass.id == class_id,
            SchoolClass.tenant_id == tenant_id,
            SchoolClass.deleted_at.is_(None),
        )
        result = await db.execute(query)
        school_class = result.scalar_one_or_none()

        if not school_class:
            raise NotFoundException("Class")

        return school_class

    async def _get_existing_record(
        self,
        db: AsyncSession,
        student_id: uuid.UUID,
        target_date: date,
    ) -> AttendanceRecord | None:
        """Check if attendance record already exists."""
        tenant_id = get_tenant_id()

        query = select(AttendanceRecord).where(
            AttendanceRecord.tenant_id == tenant_id,
            AttendanceRecord.student_id == student_id,
            AttendanceRecord.date == target_date,
        )
        result = await db.execute(query)
        return result.scalar_one_or_none()


def get_attendance_service() -> AttendanceService:
    """Get attendance service instance."""
    return AttendanceService()
