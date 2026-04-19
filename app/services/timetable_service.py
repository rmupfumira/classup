"""Timetable service: config, CRUD, smart draft generation, and views."""

import logging
import uuid
from collections import defaultdict

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.exceptions import ForbiddenException, NotFoundException, ValidationException
from app.models import (
    ClassSubject,
    SchoolClass,
    Student,
    Subject,
    TeacherClass,
    Tenant,
    Timetable,
    TimetableConfig,
    TimetableEntry,
    User,
)
from app.models.timetable import DEFAULT_DAYS, get_default_periods
from app.utils.tenant_context import get_tenant_id

logger = logging.getLogger(__name__)


class TimetableService:
    """Service for timetable management."""

    # ---------------- Feature flag guard ----------------

    async def _require_feature_enabled(self, db: AsyncSession) -> None:
        """Raise ForbiddenException if the tenant hasn't enabled timetable_management."""
        tenant_id = get_tenant_id()
        tenant = await db.get(Tenant, tenant_id)
        if not tenant:
            raise NotFoundException("Tenant")
        features = (tenant.settings or {}).get("features", {})
        if not features.get("timetable_management"):
            raise ForbiddenException("Timetable management is not enabled for this school")

    # ---------------- Config ----------------

    async def get_or_create_config(self, db: AsyncSession) -> TimetableConfig:
        """Return the tenant's timetable config, creating a default if none exists."""
        tenant_id = get_tenant_id()
        result = await db.execute(
            select(TimetableConfig).where(
                TimetableConfig.tenant_id == tenant_id,
                TimetableConfig.deleted_at.is_(None),
            )
        )
        config = result.scalar_one_or_none()
        if config:
            return config

        config = TimetableConfig(
            tenant_id=tenant_id,
            days=list(DEFAULT_DAYS),
            periods=get_default_periods(),
        )
        db.add(config)
        await db.flush()
        await db.refresh(config)
        return config

    async def update_config(
        self, db: AsyncSession, days: list[str], periods: list[dict]
    ) -> TimetableConfig:
        """Replace the days and periods for the tenant's config."""
        config = await self.get_or_create_config(db)
        config.days = days
        config.periods = periods
        await db.flush()
        await db.refresh(config)
        return config

    # ---------------- Timetable CRUD ----------------

    async def create_timetable(
        self, db: AsyncSession, class_id: uuid.UUID, name: str
    ) -> Timetable:
        """Create a new active timetable, deactivating any existing active one for the class."""
        tenant_id = get_tenant_id()

        # Verify class belongs to tenant
        cls_result = await db.execute(
            select(SchoolClass).where(
                SchoolClass.id == class_id,
                SchoolClass.tenant_id == tenant_id,
                SchoolClass.deleted_at.is_(None),
            )
        )
        school_class = cls_result.scalar_one_or_none()
        if not school_class:
            raise NotFoundException("Class")

        # Deactivate any existing active timetable for this class
        existing = await db.execute(
            select(Timetable).where(
                Timetable.class_id == class_id,
                Timetable.tenant_id == tenant_id,
                Timetable.is_active == True,  # noqa: E712
                Timetable.deleted_at.is_(None),
            )
        )
        for t in existing.scalars().all():
            t.is_active = False

        timetable = Timetable(
            tenant_id=tenant_id,
            class_id=class_id,
            name=name,
            is_active=True,
        )
        db.add(timetable)
        await db.flush()
        await db.refresh(timetable)
        return timetable

    async def get_timetable(
        self, db: AsyncSession, timetable_id: uuid.UUID
    ) -> Timetable:
        """Get a timetable with entries + relationships eagerly loaded."""
        tenant_id = get_tenant_id()
        result = await db.execute(
            select(Timetable)
            .where(
                Timetable.id == timetable_id,
                Timetable.tenant_id == tenant_id,
                Timetable.deleted_at.is_(None),
            )
            .options(
                selectinload(Timetable.school_class),
                selectinload(Timetable.entries).selectinload(TimetableEntry.subject),
                selectinload(Timetable.entries).selectinload(TimetableEntry.teacher),
            )
            .execution_options(populate_existing=True)
        )
        timetable = result.scalar_one_or_none()
        if not timetable:
            raise NotFoundException("Timetable")
        return timetable

    async def get_active_timetable_for_class(
        self, db: AsyncSession, class_id: uuid.UUID
    ) -> Timetable | None:
        """Return the active timetable for a class, or None."""
        tenant_id = get_tenant_id()
        result = await db.execute(
            select(Timetable)
            .where(
                Timetable.class_id == class_id,
                Timetable.tenant_id == tenant_id,
                Timetable.is_active == True,  # noqa: E712
                Timetable.deleted_at.is_(None),
            )
            .options(
                selectinload(Timetable.school_class),
                selectinload(Timetable.entries).selectinload(TimetableEntry.subject),
                selectinload(Timetable.entries).selectinload(TimetableEntry.teacher),
            )
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def list_timetables(
        self, db: AsyncSession, page: int = 1, page_size: int = 20
    ) -> tuple[list[Timetable], int]:
        """List timetables for the tenant."""
        tenant_id = get_tenant_id()
        base = select(Timetable).where(
            Timetable.tenant_id == tenant_id,
            Timetable.deleted_at.is_(None),
        )

        total = (
            await db.execute(select(func.count()).select_from(base.subquery()))
        ).scalar() or 0

        stmt = (
            base.options(
                selectinload(Timetable.school_class),
                selectinload(Timetable.entries),
            )
            .order_by(Timetable.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await db.execute(stmt)
        return list(result.scalars().unique().all()), total

    async def delete_timetable(
        self, db: AsyncSession, timetable_id: uuid.UUID
    ) -> None:
        """Soft delete a timetable."""
        from datetime import datetime

        timetable = await self.get_timetable(db, timetable_id)
        timetable.deleted_at = datetime.utcnow()
        timetable.is_active = False
        await db.flush()

    # ---------------- Entry CRUD ----------------

    async def set_entry(
        self,
        db: AsyncSession,
        timetable_id: uuid.UUID,
        day: str,
        period_index: int,
        subject_id: uuid.UUID,
        teacher_id: uuid.UUID | None,
    ) -> TimetableEntry:
        """Upsert a single timetable entry."""
        tenant_id = get_tenant_id()
        # Verify timetable
        timetable = await self.get_timetable(db, timetable_id)

        # Verify subject is assigned to the class
        cs_result = await db.execute(
            select(ClassSubject).where(
                ClassSubject.class_id == timetable.class_id,
                ClassSubject.subject_id == subject_id,
            )
        )
        if not cs_result.scalar_one_or_none():
            raise ValidationException([
                {"field": "subject_id", "message": "Subject is not assigned to this class"}
            ])

        # Find existing entry
        existing_q = await db.execute(
            select(TimetableEntry).where(
                TimetableEntry.timetable_id == timetable_id,
                TimetableEntry.day == day,
                TimetableEntry.period_index == period_index,
            )
        )
        entry = existing_q.scalar_one_or_none()

        if entry:
            entry.subject_id = subject_id
            entry.teacher_id = teacher_id
        else:
            entry = TimetableEntry(
                tenant_id=tenant_id,
                timetable_id=timetable_id,
                day=day,
                period_index=period_index,
                subject_id=subject_id,
                teacher_id=teacher_id,
            )
            db.add(entry)
        await db.flush()
        await db.refresh(entry)
        return entry

    async def clear_entry(
        self,
        db: AsyncSession,
        timetable_id: uuid.UUID,
        day: str,
        period_index: int,
    ) -> bool:
        """Delete a single entry. Returns True if deleted."""
        # Verify timetable ownership
        await self.get_timetable(db, timetable_id)
        result = await db.execute(
            select(TimetableEntry).where(
                TimetableEntry.timetable_id == timetable_id,
                TimetableEntry.day == day,
                TimetableEntry.period_index == period_index,
            )
        )
        entry = result.scalar_one_or_none()
        if not entry:
            return False
        await db.delete(entry)
        await db.flush()
        return True

    # ---------------- Smart draft generator ----------------

    async def generate_draft(
        self,
        db: AsyncSession,
        timetable_id: uuid.UUID,
        weekly_hours: dict[str, int] | None = None,
    ) -> tuple[int, list[str]]:
        """Auto-generate a draft timetable.

        Strategy:
        - Load the class's ClassSubjects + primary teacher assignment
        - Load the day/period config (skipping breaks)
        - Per subject, determine target slot count (weekly_hours override or even distribution)
        - Round-robin fill, spreading each subject across different days where possible
        - Skip cells where the teacher is already double-booked in another active timetable
        - Clears existing entries first

        Returns (entries_created, warnings).
        """
        tenant_id = get_tenant_id()
        timetable = await self.get_timetable(db, timetable_id)
        config = await self.get_or_create_config(db)

        # Load class subjects
        cs_result = await db.execute(
            select(ClassSubject)
            .where(ClassSubject.class_id == timetable.class_id)
            .options(selectinload(ClassSubject.subject))
            .order_by(ClassSubject.display_order)
        )
        class_subjects = list(cs_result.scalars().all())
        if not class_subjects:
            raise ValidationException([
                {"field": "subjects", "message": "This class has no subjects assigned yet. Add subjects first."}
            ])

        # Load teacher-class assignments for this class (to pick default teachers)
        tc_result = await db.execute(
            select(TeacherClass)
            .where(TeacherClass.class_id == timetable.class_id)
            .options(selectinload(TeacherClass.teacher))
        )
        teacher_classes = list(tc_result.scalars().all())

        # Pick a default teacher: primary first, else first assigned, else None
        default_teacher: User | None = None
        for tc in teacher_classes:
            if tc.is_primary:
                default_teacher = tc.teacher
                break
        if not default_teacher and teacher_classes:
            default_teacher = teacher_classes[0].teacher

        # Build list of all (day, period_index) teaching slots, skipping breaks
        days: list[str] = config.days or list(DEFAULT_DAYS)
        periods: list[dict] = config.periods or []
        teaching_slots: list[tuple[str, int]] = []
        for day in days:
            for period in periods:
                if period.get("is_break"):
                    continue
                teaching_slots.append((day, int(period["index"])))

        total_slots = len(teaching_slots)
        if total_slots == 0:
            raise ValidationException([
                {"field": "config", "message": "No teaching periods configured. Add periods in the timetable config."}
            ])

        # Determine per-subject slot counts
        weekly_hours = weekly_hours or {}
        subject_quotas: dict[uuid.UUID, int] = {}
        specified_total = 0
        for cs in class_subjects:
            requested = weekly_hours.get(str(cs.subject_id))
            if requested is not None:
                subject_quotas[cs.subject_id] = max(0, int(requested))
                specified_total += subject_quotas[cs.subject_id]

        remaining_slots = max(0, total_slots - specified_total)
        unspecified = [cs for cs in class_subjects if cs.subject_id not in subject_quotas]
        if unspecified:
            base_qty = remaining_slots // len(unspecified)
            leftover = remaining_slots % len(unspecified)
            for i, cs in enumerate(unspecified):
                subject_quotas[cs.subject_id] = base_qty + (1 if i < leftover else 0)

        # Clip quotas to not exceed total slots
        quota_total = sum(subject_quotas.values())
        if quota_total > total_slots:
            # Scale down proportionally (best-effort)
            overflow = quota_total - total_slots
            for cs in class_subjects:
                if overflow <= 0:
                    break
                cut = min(subject_quotas[cs.subject_id], overflow)
                subject_quotas[cs.subject_id] -= cut
                overflow -= cut

        # Clear existing entries
        existing = await db.execute(
            select(TimetableEntry).where(
                TimetableEntry.timetable_id == timetable_id
            )
        )
        for e in existing.scalars().all():
            await db.delete(e)
        await db.flush()

        # Load other tenants' teacher bookings (same teacher in other active timetables)
        busy_key = set()  # (teacher_id, day, period_index) already taken elsewhere
        if default_teacher:
            busy_q = await db.execute(
                select(TimetableEntry.day, TimetableEntry.period_index)
                .join(Timetable, Timetable.id == TimetableEntry.timetable_id)
                .where(
                    TimetableEntry.tenant_id == tenant_id,
                    TimetableEntry.teacher_id == default_teacher.id,
                    TimetableEntry.timetable_id != timetable_id,
                    Timetable.is_active == True,  # noqa: E712
                    Timetable.deleted_at.is_(None),
                )
            )
            for day, period_index in busy_q.all():
                busy_key.add((default_teacher.id, day, period_index))

        # Build the subject fill order: interleave subjects so no single subject
        # is stacked consecutively. Distribute across days evenly.
        fill_queue: list[uuid.UUID] = []
        per_day_cap: dict[tuple[str, uuid.UUID], int] = defaultdict(int)
        # Adaptive per-day cap: ceil(quota / num_days) so the subject can
        # actually be fully placed even if the quota > 2*days
        num_days = max(1, len(days))
        subject_day_cap: dict[uuid.UUID, int] = {
            sid: max(2, -(-quota // num_days))  # ceil division
            for sid, quota in subject_quotas.items()
        }
        # Round-robin: each round adds 1 of each subject that still has quota
        remaining = dict(subject_quotas)
        while any(v > 0 for v in remaining.values()):
            for cs in class_subjects:
                if remaining.get(cs.subject_id, 0) > 0:
                    fill_queue.append(cs.subject_id)
                    remaining[cs.subject_id] -= 1

        # Fill the slots
        warnings: list[str] = []
        entries_created = 0
        slot_idx = 0
        unfilled_subjects: list[uuid.UUID] = []
        # Track claimed slots in-memory (pending adds don't appear in SELECT
        # until flushed, so we must track ourselves)
        claimed_slots: set[tuple[str, int]] = set()

        for subject_id in fill_queue:
            placed = False
            attempts = 0
            while attempts < len(teaching_slots):
                if slot_idx >= len(teaching_slots):
                    slot_idx = 0
                day, period_index = teaching_slots[slot_idx]
                slot_idx += 1
                attempts += 1

                # Skip if slot already claimed in this run
                if (day, period_index) in claimed_slots:
                    continue

                # Skip if teacher is booked elsewhere at this slot
                if default_teacher and (default_teacher.id, day, period_index) in busy_key:
                    continue

                # Prefer to avoid stacking the same subject more than the
                # adaptive cap per day (so subjects spread across days).
                if per_day_cap[(day, subject_id)] >= subject_day_cap.get(subject_id, 2):
                    continue

                # Place it
                entry = TimetableEntry(
                    tenant_id=tenant_id,
                    timetable_id=timetable_id,
                    day=day,
                    period_index=period_index,
                    subject_id=subject_id,
                    teacher_id=default_teacher.id if default_teacher else None,
                )
                db.add(entry)
                entries_created += 1
                per_day_cap[(day, subject_id)] += 1
                claimed_slots.add((day, period_index))
                placed = True
                break

            if not placed:
                unfilled_subjects.append(subject_id)

        await db.flush()

        if unfilled_subjects:
            # Build friendly warning with subject names
            subj_names = {cs.subject_id: cs.subject.name for cs in class_subjects}
            missed = [subj_names.get(sid, "Unknown") for sid in unfilled_subjects]
            from collections import Counter
            counts = Counter(missed)
            details = ", ".join(f"{name} × {c}" for name, c in counts.items())
            warnings.append(
                f"Could not place {len(unfilled_subjects)} slot(s) due to teacher conflicts or cap: {details}"
            )

        if not default_teacher:
            warnings.append(
                "No teacher is assigned to this class — slots were created without a teacher. Assign teachers to the class and re-generate."
            )

        logger.info(
            f"Generated draft for timetable {timetable_id}: {entries_created} entries, "
            f"{len(warnings)} warnings"
        )
        return entries_created, warnings

    # ---------------- Views ----------------

    async def get_teacher_timetable(
        self, db: AsyncSession, teacher_id: uuid.UUID
    ) -> list[TimetableEntry]:
        """Return all lessons for a teacher across active timetables."""
        tenant_id = get_tenant_id()
        result = await db.execute(
            select(TimetableEntry)
            .join(Timetable, Timetable.id == TimetableEntry.timetable_id)
            .where(
                TimetableEntry.tenant_id == tenant_id,
                TimetableEntry.teacher_id == teacher_id,
                Timetable.is_active == True,  # noqa: E712
                Timetable.deleted_at.is_(None),
            )
            .options(
                selectinload(TimetableEntry.subject),
                selectinload(TimetableEntry.timetable).selectinload(Timetable.school_class),
            )
            .order_by(TimetableEntry.day, TimetableEntry.period_index)
        )
        return list(result.scalars().unique().all())

    async def get_student_timetable(
        self, db: AsyncSession, student_id: uuid.UUID
    ) -> Timetable | None:
        """Return the active timetable for a student's class, or None."""
        tenant_id = get_tenant_id()
        stu_result = await db.execute(
            select(Student).where(
                Student.id == student_id,
                Student.tenant_id == tenant_id,
                Student.deleted_at.is_(None),
            )
        )
        student = stu_result.scalar_one_or_none()
        if not student or not student.class_id:
            return None
        return await self.get_active_timetable_for_class(db, student.class_id)

    async def detect_conflicts(
        self, db: AsyncSession, timetable_id: uuid.UUID
    ) -> set[tuple[str, int]]:
        """Return set of (day, period_index) where the entry's teacher is double-booked."""
        tenant_id = get_tenant_id()
        timetable = await self.get_timetable(db, timetable_id)

        conflicts: set[tuple[str, int]] = set()
        for entry in timetable.entries:
            if not entry.teacher_id:
                continue
            clash_q = await db.execute(
                select(func.count(TimetableEntry.id))
                .join(Timetable, Timetable.id == TimetableEntry.timetable_id)
                .where(
                    TimetableEntry.tenant_id == tenant_id,
                    TimetableEntry.teacher_id == entry.teacher_id,
                    TimetableEntry.day == entry.day,
                    TimetableEntry.period_index == entry.period_index,
                    TimetableEntry.id != entry.id,
                    Timetable.is_active == True,  # noqa: E712
                    Timetable.deleted_at.is_(None),
                )
            )
            count = clash_q.scalar() or 0
            if count > 0:
                conflicts.add((entry.day, entry.period_index))
        return conflicts


_timetable_service: TimetableService | None = None


def get_timetable_service() -> TimetableService:
    """Get the timetable service singleton."""
    global _timetable_service
    if _timetable_service is None:
        _timetable_service = TimetableService()
    return _timetable_service
