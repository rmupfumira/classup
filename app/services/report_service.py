"""Report service for managing reports and templates."""

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.grade_level import GradeLevel
from app.models.report import DailyReport, ReportStatus, ReportTemplate, ReportTemplateGradeLevel
from app.models.student import Student
from app.schemas.report import (
    ReportCreate,
    ReportTemplateCreate,
    ReportTemplateUpdate,
    ReportUpdate,
)
from app.utils.tenant_context import get_current_user_id, get_tenant_id


class ReportService:
    """Service for managing reports and templates."""

    # ============== Template Methods ==============

    async def get_templates(
        self,
        db: AsyncSession,
        is_active: bool | None = None,
        report_type: str | None = None,
        grade_level_id: uuid.UUID | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[ReportTemplate], int]:
        """Get all report templates for the current tenant."""
        tenant_id = get_tenant_id()

        # Build query with grade_levels relationship loaded
        query = (
            select(ReportTemplate)
            .options(selectinload(ReportTemplate.grade_levels))
            .where(
                ReportTemplate.tenant_id == tenant_id,
                ReportTemplate.deleted_at.is_(None),
            )
        )

        if is_active is not None:
            query = query.where(ReportTemplate.is_active == is_active)

        if report_type:
            query = query.where(ReportTemplate.report_type == report_type)

        # Filter by grade_level_id if provided
        if grade_level_id:
            query = query.join(ReportTemplateGradeLevel).where(
                ReportTemplateGradeLevel.grade_level_id == grade_level_id
            )

        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_query)).scalar() or 0

        # Apply pagination and ordering
        query = query.order_by(
            ReportTemplate.display_order,
            ReportTemplate.name,
        )
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(query)
        templates = list(result.scalars().all())

        return templates, total

    async def get_template(
        self,
        db: AsyncSession,
        template_id: uuid.UUID,
    ) -> ReportTemplate | None:
        """Get a specific report template."""
        tenant_id = get_tenant_id()

        query = (
            select(ReportTemplate)
            .options(selectinload(ReportTemplate.grade_levels))
            .where(
                ReportTemplate.id == template_id,
                ReportTemplate.tenant_id == tenant_id,
                ReportTemplate.deleted_at.is_(None),
            )
        )

        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def get_templates_for_student(
        self,
        db: AsyncSession,
        student_id: uuid.UUID,
    ) -> list[ReportTemplate]:
        """Get all applicable templates for a specific student.

        Uses the FK-based grade_level_id from the student's class if available,
        falls back to legacy string-based matching for backward compatibility.
        """
        tenant_id = get_tenant_id()

        # Get student with class relationship
        student_query = (
            select(Student)
            .options(selectinload(Student.school_class))
            .where(
                Student.id == student_id,
                Student.tenant_id == tenant_id,
                Student.deleted_at.is_(None),
            )
        )
        student_result = await db.execute(student_query)
        student = student_result.scalar_one_or_none()

        if not student:
            return []

        # Get all active templates with grade_levels loaded
        query = (
            select(ReportTemplate)
            .options(selectinload(ReportTemplate.grade_levels))
            .where(
                ReportTemplate.tenant_id == tenant_id,
                ReportTemplate.is_active == True,
                ReportTemplate.deleted_at.is_(None),
            )
            .order_by(ReportTemplate.display_order)
        )

        result = await db.execute(query)
        all_templates = list(result.scalars().all())

        # Get grade_level_id from student's class
        student_grade_level_id = None
        if student.school_class and student.school_class.grade_level_id:
            student_grade_level_id = student.school_class.grade_level_id

        # Filter to applicable templates
        applicable = []
        for template in all_templates:
            # If template has FK-based grade_levels, use that
            if template.grade_levels:
                # Universal template if no grade levels specified (empty list won't reach here)
                grade_level_ids = [gl.id for gl in template.grade_levels]
                if student_grade_level_id and student_grade_level_id in grade_level_ids:
                    applicable.append(template)
                # If no student grade level but template requires one, skip
            elif not template.applies_to_grade_level:
                # Universal template (no FK grade levels AND no legacy string)
                applicable.append(template)
            else:
                # Fall back to legacy string-based matching
                if template.applies_to_student(student.age_group, student.grade_level):
                    applicable.append(template)

        return applicable

    async def create_template(
        self,
        db: AsyncSession,
        data: ReportTemplateCreate,
    ) -> ReportTemplate:
        """Create a new report template."""
        tenant_id = get_tenant_id()

        template = ReportTemplate(
            tenant_id=tenant_id,
            name=data.name,
            description=data.description,
            report_type=data.report_type.value,
            frequency=data.frequency.value,
            applies_to_grade_level=data.applies_to_grade_level,  # DEPRECATED
            sections=[section.model_dump() for section in data.sections],
            display_order=data.display_order,
            is_active=data.is_active,
        )

        db.add(template)
        await db.flush()

        # Handle grade_level_ids if provided
        if data.grade_level_ids:
            await self._set_template_grade_levels(db, template.id, data.grade_level_ids, tenant_id)

        await db.commit()
        await db.refresh(template)

        # Reload with grade_levels relationship
        return await self.get_template(db, template.id)

    async def update_template(
        self,
        db: AsyncSession,
        template_id: uuid.UUID,
        data: ReportTemplateUpdate,
    ) -> ReportTemplate | None:
        """Update a report template."""
        tenant_id = get_tenant_id()
        template = await self.get_template(db, template_id)
        if not template:
            return None

        if data.name is not None:
            template.name = data.name
        if data.description is not None:
            template.description = data.description
        if data.report_type is not None:
            template.report_type = data.report_type.value
        if data.frequency is not None:
            template.frequency = data.frequency.value
        if data.applies_to_grade_level is not None:
            template.applies_to_grade_level = data.applies_to_grade_level
        if data.sections is not None:
            template.sections = [section.model_dump() for section in data.sections]
        if data.display_order is not None:
            template.display_order = data.display_order
        if data.is_active is not None:
            template.is_active = data.is_active

        # Handle grade_level_ids if provided
        if data.grade_level_ids is not None:
            await self._set_template_grade_levels(db, template_id, data.grade_level_ids, tenant_id)

        await db.commit()

        # Reload with grade_levels relationship
        return await self.get_template(db, template_id)

    async def delete_template(
        self,
        db: AsyncSession,
        template_id: uuid.UUID,
    ) -> bool:
        """Soft delete a report template."""
        template = await self.get_template(db, template_id)
        if not template:
            return False

        template.deleted_at = datetime.now(timezone.utc)
        template.is_active = False
        await db.commit()

        return True

    async def _set_template_grade_levels(
        self,
        db: AsyncSession,
        template_id: uuid.UUID,
        grade_level_ids: list[uuid.UUID],
        tenant_id: uuid.UUID,
    ) -> None:
        """Set the grade levels for a template (replaces existing)."""
        # Delete existing grade level associations
        delete_query = select(ReportTemplateGradeLevel).where(
            ReportTemplateGradeLevel.template_id == template_id
        )
        result = await db.execute(delete_query)
        for existing in result.scalars():
            await db.delete(existing)

        # Verify grade levels belong to tenant and create new associations
        for grade_level_id in grade_level_ids:
            # Verify grade level exists and belongs to tenant
            gl_query = select(GradeLevel).where(
                GradeLevel.id == grade_level_id,
                GradeLevel.tenant_id == tenant_id,
                GradeLevel.deleted_at.is_(None),
            )
            gl_result = await db.execute(gl_query)
            grade_level = gl_result.scalar_one_or_none()

            if grade_level:
                association = ReportTemplateGradeLevel(
                    template_id=template_id,
                    grade_level_id=grade_level_id,
                )
                db.add(association)

        await db.flush()

    # ============== Report Methods ==============

    async def get_reports(
        self,
        db: AsyncSession,
        class_id: uuid.UUID | None = None,
        student_id: uuid.UUID | None = None,
        template_id: uuid.UUID | None = None,
        report_date: date | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict], int]:
        """Get reports with optional filters."""
        tenant_id = get_tenant_id()

        # Build query with joins
        query = (
            select(DailyReport)
            .options(
                selectinload(DailyReport.student),
                selectinload(DailyReport.school_class),
                selectinload(DailyReport.template),
                selectinload(DailyReport.created_by_user),
            )
            .where(
                DailyReport.tenant_id == tenant_id,
                DailyReport.deleted_at.is_(None),
            )
        )

        if class_id:
            query = query.where(DailyReport.class_id == class_id)
        if student_id:
            query = query.where(DailyReport.student_id == student_id)
        if template_id:
            query = query.where(DailyReport.template_id == template_id)
        if report_date:
            query = query.where(DailyReport.report_date == report_date)
        if status:
            query = query.where(DailyReport.status == status)

        # Get total count
        count_query = select(func.count()).select_from(
            select(DailyReport.id)
            .where(
                DailyReport.tenant_id == tenant_id,
                DailyReport.deleted_at.is_(None),
            )
            .subquery()
        )

        # Re-apply filters for count
        count_base = select(DailyReport.id).where(
            DailyReport.tenant_id == tenant_id,
            DailyReport.deleted_at.is_(None),
        )
        if class_id:
            count_base = count_base.where(DailyReport.class_id == class_id)
        if student_id:
            count_base = count_base.where(DailyReport.student_id == student_id)
        if template_id:
            count_base = count_base.where(DailyReport.template_id == template_id)
        if report_date:
            count_base = count_base.where(DailyReport.report_date == report_date)
        if status:
            count_base = count_base.where(DailyReport.status == status)

        count_query = select(func.count()).select_from(count_base.subquery())
        total = (await db.execute(count_query)).scalar() or 0

        # Apply pagination and ordering
        query = query.order_by(DailyReport.report_date.desc(), DailyReport.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(query)
        reports = list(result.scalars().all())

        # Format response
        report_list = []
        for report in reports:
            report_list.append({
                "id": report.id,
                "student_id": report.student_id,
                "student_name": f"{report.student.first_name} {report.student.last_name}" if report.student else None,
                "class_id": report.class_id,
                "class_name": report.school_class.name if report.school_class else None,
                "template_id": report.template_id,
                "template_name": report.template.name if report.template else None,
                "report_type": report.template.report_type if report.template else None,
                "report_date": report.report_date,
                "status": report.status,
                "finalized_at": report.finalized_at,
                "created_at": report.created_at,
            })

        return report_list, total

    async def get_report(
        self,
        db: AsyncSession,
        report_id: uuid.UUID,
    ) -> DailyReport | None:
        """Get a specific report with all relationships."""
        tenant_id = get_tenant_id()

        query = (
            select(DailyReport)
            .options(
                selectinload(DailyReport.student),
                selectinload(DailyReport.school_class),
                selectinload(DailyReport.template),
                selectinload(DailyReport.created_by_user),
            )
            .where(
                DailyReport.id == report_id,
                DailyReport.tenant_id == tenant_id,
                DailyReport.deleted_at.is_(None),
            )
        )

        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def get_student_reports(
        self,
        db: AsyncSession,
        student_id: uuid.UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict], int]:
        """Get all reports for a specific student."""
        return await self.get_reports(
            db,
            student_id=student_id,
            page=page,
            page_size=page_size,
        )

    async def get_existing_report(
        self,
        db: AsyncSession,
        student_id: uuid.UUID,
        template_id: uuid.UUID,
        report_date: date,
    ) -> DailyReport | None:
        """Check if a report already exists for the student/template/date combination."""
        tenant_id = get_tenant_id()

        query = select(DailyReport).where(
            DailyReport.tenant_id == tenant_id,
            DailyReport.student_id == student_id,
            DailyReport.template_id == template_id,
            DailyReport.report_date == report_date,
            DailyReport.deleted_at.is_(None),
        )

        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def create_report(
        self,
        db: AsyncSession,
        data: ReportCreate,
    ) -> DailyReport:
        """Create a new report."""
        tenant_id = get_tenant_id()
        user_id = get_current_user_id()

        report = DailyReport(
            tenant_id=tenant_id,
            student_id=data.student_id,
            class_id=data.class_id,
            template_id=data.template_id,
            report_date=data.report_date,
            report_data=data.report_data,
            status=ReportStatus.DRAFT.value,
            created_by=user_id,
        )

        db.add(report)
        await db.commit()
        await db.refresh(report)

        # Reload with relationships
        return await self.get_report(db, report.id)

    async def update_report(
        self,
        db: AsyncSession,
        report_id: uuid.UUID,
        data: ReportUpdate,
    ) -> DailyReport | None:
        """Update a report (only if still draft)."""
        report = await self.get_report(db, report_id)
        if not report:
            return None

        if report.is_finalized:
            raise ValueError("Cannot update a finalized report")

        report.report_data = data.report_data
        await db.commit()
        await db.refresh(report)

        return report

    async def finalize_report(
        self,
        db: AsyncSession,
        report_id: uuid.UUID,
        notify_parents: bool = True,
    ) -> DailyReport | None:
        """Finalize a report and optionally notify parents."""
        report = await self.get_report(db, report_id)
        if not report:
            return None

        if report.is_finalized:
            raise ValueError("Report is already finalized")

        report.status = ReportStatus.FINALIZED.value
        report.finalized_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(report)

        # TODO: Send notifications to parents if notify_parents is True
        # This will be implemented in Phase 8 with email/WhatsApp integration

        return report

    async def delete_report(
        self,
        db: AsyncSession,
        report_id: uuid.UUID,
    ) -> bool:
        """Soft delete a report (only if still draft)."""
        report = await self.get_report(db, report_id)
        if not report:
            return False

        if report.is_finalized:
            raise ValueError("Cannot delete a finalized report")

        report.deleted_at = datetime.now(timezone.utc)
        await db.commit()

        return True

    # ============== Statistics Methods ==============

    async def get_report_stats(
        self,
        db: AsyncSession,
        class_id: uuid.UUID | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict:
        """Get report statistics for the tenant."""
        tenant_id = get_tenant_id()

        # Base query
        base_conditions = [
            DailyReport.tenant_id == tenant_id,
            DailyReport.deleted_at.is_(None),
        ]

        if class_id:
            base_conditions.append(DailyReport.class_id == class_id)
        if start_date:
            base_conditions.append(DailyReport.report_date >= start_date)
        if end_date:
            base_conditions.append(DailyReport.report_date <= end_date)

        # Total reports
        total_query = select(func.count(DailyReport.id)).where(*base_conditions)
        total = (await db.execute(total_query)).scalar() or 0

        # Draft reports
        draft_query = select(func.count(DailyReport.id)).where(
            *base_conditions,
            DailyReport.status == ReportStatus.DRAFT.value,
        )
        draft_count = (await db.execute(draft_query)).scalar() or 0

        # Finalized reports
        finalized_query = select(func.count(DailyReport.id)).where(
            *base_conditions,
            DailyReport.status == ReportStatus.FINALIZED.value,
        )
        finalized_count = (await db.execute(finalized_query)).scalar() or 0

        return {
            "total_reports": total,
            "draft_reports": draft_count,
            "finalized_reports": finalized_count,
        }


# Singleton instance
_report_service: ReportService | None = None


def get_report_service() -> ReportService:
    """Get the report service singleton."""
    global _report_service
    if _report_service is None:
        _report_service = ReportService()
    return _report_service
