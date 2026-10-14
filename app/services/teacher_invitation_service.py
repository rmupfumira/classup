"""Teacher invitation service."""

import logging
import secrets
import string
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import ClassSubject, SchoolClass, Tenant, User
from app.models.academic import ClassSubjectTeacher
from app.models.school_class import TeacherClass
from app.models.teacher_invitation import TeacherInvitation
from app.utils.tenant_context import get_current_user_id, get_tenant_id

logger = logging.getLogger(__name__)
settings = get_settings()


class TeacherInvitationService:
    """Service for managing teacher invitations."""

    def _generate_code(self, length: int = 8) -> str:
        """Generate a random alphanumeric invitation code."""
        alphabet = string.ascii_uppercase + string.digits
        alphabet = alphabet.replace("0", "").replace("O", "").replace("I", "").replace("1", "")
        return "".join(secrets.choice(alphabet) for _ in range(length))

    async def _validate_assignments(
        self,
        db: AsyncSession,
        tenant: Tenant,
        assignments: list[dict] | None,
    ) -> list[dict]:
        """Validate a list of intended assignments against the tenant.

        Rules:
          - Each ``class_id`` must exist on this tenant (not deleted).
          - Each ``subject_id`` must be attached to that class via
            ``class_subjects``.
          - If any item has subject_ids AND the tenant has
            ``subject_teacher_enabled`` off, reject with a ValueError
            — the admin needs to turn the mode on first (or drop the
            subject assignments).

        Returns the normalised list (uuids stringified, defaults
        filled in, deduped subject_ids per item). Empty list in →
        empty list out.
        """
        if not assignments:
            return []

        tenant_id = tenant.id

        # Collect & validate class ids in one round-trip.
        class_ids: set[UUID] = set()
        normalised: list[dict] = []
        for item in assignments:
            if not isinstance(item, dict):
                raise ValueError(
                    "Each assignment must be an object with "
                    "class_id / is_primary / subject_ids",
                )
            raw_cid = item.get("class_id")
            if raw_cid is None:
                raise ValueError("Assignment missing class_id")
            try:
                cid = UUID(str(raw_cid))
            except (TypeError, ValueError):
                raise ValueError(f"Invalid class_id: {raw_cid}")

            is_primary = bool(item.get("is_primary", False))
            raw_sids = item.get("subject_ids") or []
            if not isinstance(raw_sids, (list, tuple)):
                raise ValueError("subject_ids must be a list")
            sids: list[UUID] = []
            seen: set[UUID] = set()
            for raw_sid in raw_sids:
                try:
                    sid = UUID(str(raw_sid))
                except (TypeError, ValueError):
                    raise ValueError(f"Invalid subject_id: {raw_sid}")
                if sid in seen:
                    continue
                seen.add(sid)
                sids.append(sid)

            if sids and not tenant.subject_teacher_enabled:
                raise ValueError(
                    "Subject-specialist mode is off for this tenant — "
                    "remove subject assignments or enable the mode first."
                )

            class_ids.add(cid)
            normalised.append({
                "class_id": str(cid),
                "is_primary": is_primary,
                "subject_ids": [str(s) for s in sids],
                "_cid": cid,
                "_sids": sids,
            })

        if not class_ids:
            return []

        # Verify every class belongs to this tenant.
        valid_classes = {
            row.id: row for row in (await db.execute(
                select(SchoolClass).where(
                    SchoolClass.id.in_(class_ids),
                    SchoolClass.tenant_id == tenant_id,
                    SchoolClass.deleted_at.is_(None),
                )
            )).scalars().all()
        }
        missing = class_ids - set(valid_classes.keys())
        if missing:
            raise ValueError(
                f"Class not found on this tenant: {next(iter(missing))}"
            )

        # Fetch the class_subjects rows for every (class, subject) pair
        # we care about so we can validate subject attachment in one go.
        all_pairs: list[tuple[UUID, UUID]] = []
        for item in normalised:
            for sid in item["_sids"]:
                all_pairs.append((item["_cid"], sid))

        if all_pairs:
            unique_subj_ids = {p[1] for p in all_pairs}
            rows = (await db.execute(
                select(ClassSubject).where(
                    ClassSubject.class_id.in_({p[0] for p in all_pairs}),
                    ClassSubject.subject_id.in_(unique_subj_ids),
                )
            )).scalars().all()
            attached = {(r.class_id, r.subject_id) for r in rows}
            for cid, sid in all_pairs:
                if (cid, sid) not in attached:
                    raise ValueError(
                        f"Subject {sid} is not assigned to class {cid}"
                    )

        # Strip the UUID helper fields before storing.
        return [
            {
                "class_id": item["class_id"],
                "is_primary": item["is_primary"],
                "subject_ids": item["subject_ids"],
            }
            for item in normalised
        ]

    async def create_invitation(
        self,
        db: AsyncSession,
        email: str,
        first_name: str,
        last_name: str = "",
        intended_assignments: list[dict] | None = None,
    ) -> TeacherInvitation:
        """Create a new teacher invitation and return it.

        ``intended_assignments`` is an optional pre-plan of the
        teacher's TeacherClass + ClassSubjectTeacher rows — see the
        ``intended_assignments`` column docstring on
        :class:`TeacherInvitation` for the shape. Validated here
        against the current tenant; stored on the invitation so the
        acceptance flow can apply them atomically.
        """
        tenant_id = get_tenant_id()
        user_id = get_current_user_id()

        email = email.lower().strip()

        # Check if teacher already exists with this email
        existing_user = await db.execute(
            select(User).where(
                User.tenant_id == tenant_id,
                User.email == email,
                User.deleted_at.is_(None),
            )
        )
        if existing_user.scalar_one_or_none():
            raise ValueError("A user with this email already exists")

        # Check for existing pending invitation
        existing_inv = await db.execute(
            select(TeacherInvitation).where(
                and_(
                    TeacherInvitation.tenant_id == tenant_id,
                    TeacherInvitation.email == email,
                    TeacherInvitation.status == "PENDING",
                )
            )
        )
        if existing_inv.scalar_one_or_none():
            raise ValueError("A pending invitation already exists for this email")

        tenant = await db.get(Tenant, tenant_id)
        if tenant is None:
            raise ValueError("Tenant not found")

        normalised_assignments = await self._validate_assignments(
            db, tenant, intended_assignments,
        )

        # Generate unique code
        code = self._generate_code()
        while True:
            check = await db.execute(
                select(TeacherInvitation).where(
                    TeacherInvitation.invitation_code == code
                )
            )
            if not check.scalar_one_or_none():
                break
            code = self._generate_code()

        expires_at = datetime.now(timezone.utc) + timedelta(
            days=settings.invitation_code_expiry_days
        )

        invitation = TeacherInvitation(
            tenant_id=tenant_id,
            email=email,
            first_name=first_name.strip(),
            last_name=last_name.strip(),
            invitation_code=code,
            status="PENDING",
            created_by=user_id,
            expires_at=expires_at,
            intended_assignments=normalised_assignments,
        )

        db.add(invitation)
        await db.commit()
        await db.refresh(invitation)

        logger.info(
            f"Created teacher invitation {invitation.id} for {email} "
            f"with {len(normalised_assignments)} intended assignment(s)"
        )
        return invitation

    async def update_intended_assignments(
        self,
        db: AsyncSession,
        invitation_id: UUID,
        assignments: list[dict] | None,
    ) -> TeacherInvitation:
        """Replace the stored ``intended_assignments`` on a pending
        invitation.

        The admin edits the plan from the Teachers list before the
        teacher accepts. Rejects if the invitation isn't pending, or
        if validation fails (same rules as ``create_invitation``).
        """
        tenant_id = get_tenant_id()
        result = await db.execute(
            select(TeacherInvitation).where(
                TeacherInvitation.id == invitation_id,
                TeacherInvitation.tenant_id == tenant_id,
            )
        )
        invitation = result.scalar_one_or_none()
        if invitation is None:
            raise ValueError("Invitation not found")
        if invitation.status != "PENDING":
            raise ValueError(
                "Only pending invitations can have their assignments edited"
            )

        tenant = await db.get(Tenant, tenant_id)
        if tenant is None:
            raise ValueError("Tenant not found")

        normalised = await self._validate_assignments(
            db, tenant, assignments,
        )
        invitation.intended_assignments = normalised
        await db.commit()
        await db.refresh(invitation)
        return invitation

    async def _apply_intended_assignments(
        self,
        db: AsyncSession,
        invitation: TeacherInvitation,
        teacher_id: UUID,
    ) -> dict:
        """Create TeacherClass + ClassSubjectTeacher rows from the
        invitation's ``intended_assignments``.

        Idempotent: skips rows that already exist (so a replay / crash
        recovery doesn't double-link anything). Never raises — logs
        per-assignment failures and keeps going so a single bad plan
        doesn't block the whole acceptance.

        Returns a summary ``{"created_general": n, "created_subject": n,
        "errors": [...]}`` for the caller to log.
        """
        summary = {
            "created_general": 0,
            "created_subject": 0,
            "errors": [],
        }
        if not invitation.intended_assignments:
            return summary

        tenant_id = invitation.tenant_id

        # Pre-fetch classes to validate they still exist (admin could
        # have deleted one between invite and acceptance).
        class_ids: set[UUID] = set()
        for item in invitation.intended_assignments:
            try:
                class_ids.add(UUID(str(item.get("class_id"))))
            except (TypeError, ValueError):
                continue
        valid_classes: set[UUID] = set()
        if class_ids:
            rows = (await db.execute(
                select(SchoolClass.id).where(
                    SchoolClass.id.in_(class_ids),
                    SchoolClass.tenant_id == tenant_id,
                    SchoolClass.deleted_at.is_(None),
                )
            )).scalars().all()
            valid_classes = set(rows)

        for item in invitation.intended_assignments:
            try:
                cid = UUID(str(item.get("class_id")))
            except (TypeError, ValueError):
                summary["errors"].append({
                    "item": item,
                    "error": "invalid class_id",
                })
                continue
            if cid not in valid_classes:
                summary["errors"].append({
                    "item": item,
                    "error": "class no longer exists",
                })
                continue

            is_primary = bool(item.get("is_primary", False))
            raw_sids = item.get("subject_ids") or []

            # General class teacher row (one per class, idempotent).
            if is_primary or not raw_sids:
                existing_tc = (await db.execute(
                    select(TeacherClass).where(
                        TeacherClass.teacher_id == teacher_id,
                        TeacherClass.class_id == cid,
                    )
                )).scalar_one_or_none()
                if existing_tc is None:
                    # If marking as primary, unset other primaries on
                    # this class first.
                    if is_primary:
                        other_primaries = (await db.execute(
                            select(TeacherClass).where(
                                TeacherClass.class_id == cid,
                                TeacherClass.is_primary.is_(True),
                            )
                        )).scalars().all()
                        for other in other_primaries:
                            other.is_primary = False
                    db.add(TeacherClass(
                        teacher_id=teacher_id,
                        class_id=cid,
                        is_primary=is_primary,
                    ))
                    summary["created_general"] += 1
                else:
                    # Already linked — flip primary flag if requested
                    # and no other row holds it.
                    if is_primary and not existing_tc.is_primary:
                        other_primaries = (await db.execute(
                            select(TeacherClass).where(
                                TeacherClass.class_id == cid,
                                TeacherClass.is_primary.is_(True),
                                TeacherClass.teacher_id != teacher_id,
                            )
                        )).scalars().all()
                        for other in other_primaries:
                            other.is_primary = False
                        existing_tc.is_primary = True

            # Subject-specialist rows (one per subject, idempotent).
            for raw_sid in raw_sids:
                try:
                    sid = UUID(str(raw_sid))
                except (TypeError, ValueError):
                    summary["errors"].append({
                        "item": item,
                        "subject_id": raw_sid,
                        "error": "invalid subject_id",
                    })
                    continue

                # Guard: the subject must still be attached to the class.
                cs = (await db.execute(
                    select(ClassSubject).where(
                        ClassSubject.class_id == cid,
                        ClassSubject.subject_id == sid,
                    )
                )).scalar_one_or_none()
                if cs is None:
                    summary["errors"].append({
                        "item": item,
                        "subject_id": str(sid),
                        "error": "subject no longer on class",
                    })
                    continue

                existing_cst = (await db.execute(
                    select(ClassSubjectTeacher).where(
                        ClassSubjectTeacher.teacher_id == teacher_id,
                        ClassSubjectTeacher.class_id == cid,
                        ClassSubjectTeacher.subject_id == sid,
                    )
                )).scalar_one_or_none()
                if existing_cst is None:
                    db.add(ClassSubjectTeacher(
                        teacher_id=teacher_id,
                        class_id=cid,
                        subject_id=sid,
                    ))
                    summary["created_subject"] += 1

        await db.flush()
        return summary

    async def get_invitation_by_code(
        self,
        db: AsyncSession,
        code: str,
    ) -> TeacherInvitation | None:
        """Get a teacher invitation by code (no tenant context required)."""
        result = await db.execute(
            select(TeacherInvitation).where(
                TeacherInvitation.invitation_code == code.upper()
            )
        )
        return result.scalar_one_or_none()

    async def verify_invitation(
        self,
        db: AsyncSession,
        code: str,
        email: str,
    ) -> dict:
        """Verify a teacher invitation code and email match."""
        invitation = await self.get_invitation_by_code(db, code)

        if not invitation:
            return {"valid": False, "message": "Invalid invitation code"}

        if invitation.email.lower() != email.lower():
            return {"valid": False, "message": "Email does not match invitation"}

        if invitation.status == "ACCEPTED":
            return {"valid": False, "message": "This invitation has already been used"}

        if invitation.status == "EXPIRED" or invitation.expires_at < datetime.now(
            timezone.utc
        ):
            if invitation.status != "EXPIRED":
                invitation.status = "EXPIRED"
                await db.commit()
            return {"valid": False, "message": "This invitation has expired"}

        tenant = await db.get(Tenant, invitation.tenant_id)

        return {
            "valid": True,
            "message": "Invitation is valid",
            "school_name": tenant.name if tenant else None,
            "email": invitation.email,
            "first_name": invitation.first_name,
            "last_name": invitation.last_name,
            "role": "TEACHER",
        }

    async def accept_invitation(
        self,
        db: AsyncSession,
        code: str,
        teacher_id: UUID,
    ) -> bool:
        """Mark a teacher invitation as accepted and apply any
        pre-planned class/subject assignments.

        Called from the auth service after the teacher's User row has
        been created (``register_teacher``). Idempotent — applying the
        same assignments twice is a no-op.
        """
        invitation = await self.get_invitation_by_code(db, code)
        if not invitation or invitation.status != "PENDING":
            return False

        invitation.status = "ACCEPTED"
        invitation.accepted_at = datetime.now(timezone.utc)

        # Apply intended assignments (TeacherClass + ClassSubjectTeacher)
        # before commit so everything lives in one transaction. Logs
        # per-assignment errors but never raises — a single bad plan
        # should not block acceptance.
        try:
            summary = await self._apply_intended_assignments(
                db, invitation, teacher_id,
            )
            if summary["created_general"] or summary["created_subject"]:
                logger.info(
                    "Teacher invitation %s applied %d general + %d subject "
                    "assignment(s) for teacher %s",
                    invitation.id,
                    summary["created_general"],
                    summary["created_subject"],
                    teacher_id,
                )
            if summary["errors"]:
                logger.warning(
                    "Teacher invitation %s had %d assignment errors: %s",
                    invitation.id, len(summary["errors"]), summary["errors"],
                )
        except Exception:
            logger.exception(
                "Failed to apply intended_assignments for invitation %s",
                invitation.id,
            )

        await db.commit()
        logger.info(
            f"Teacher invitation {invitation.id} accepted by user {teacher_id}"
        )
        return True

    async def list_invitations(
        self,
        db: AsyncSession,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[TeacherInvitation], int]:
        """List teacher invitations with optional filters."""
        tenant_id = get_tenant_id()

        query = select(TeacherInvitation).where(
            TeacherInvitation.tenant_id == tenant_id
        )

        if status:
            query = query.where(TeacherInvitation.status == status)

        count_query = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_query)).scalar() or 0

        query = query.order_by(TeacherInvitation.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(query)
        invitations = list(result.scalars().all())

        return invitations, total

    async def cancel_invitation(
        self,
        db: AsyncSession,
        invitation_id: UUID,
    ) -> bool:
        """Cancel a teacher invitation."""
        tenant_id = get_tenant_id()

        result = await db.execute(
            select(TeacherInvitation).where(
                and_(
                    TeacherInvitation.id == invitation_id,
                    TeacherInvitation.tenant_id == tenant_id,
                )
            )
        )
        invitation = result.scalar_one_or_none()
        if not invitation:
            return False

        await db.delete(invitation)
        await db.commit()
        logger.info(f"Teacher invitation {invitation_id} cancelled")
        return True

    async def resend_invitation(
        self,
        db: AsyncSession,
        invitation_id: UUID,
    ) -> TeacherInvitation | None:
        """Resend a teacher invitation with a new code."""
        tenant_id = get_tenant_id()

        result = await db.execute(
            select(TeacherInvitation).where(
                and_(
                    TeacherInvitation.id == invitation_id,
                    TeacherInvitation.tenant_id == tenant_id,
                )
            )
        )
        invitation = result.scalar_one_or_none()
        if not invitation:
            return None

        if invitation.status == "ACCEPTED":
            raise ValueError("Cannot resend an already accepted invitation")

        # Reset status to PENDING if it was expired
        invitation.status = "PENDING"

        invitation.invitation_code = self._generate_code()
        invitation.expires_at = datetime.now(timezone.utc) + timedelta(
            days=settings.invitation_code_expiry_days
        )

        await db.commit()
        await db.refresh(invitation)

        logger.info(f"Teacher invitation {invitation_id} resent with new code")
        return invitation


_teacher_invitation_service: TeacherInvitationService | None = None


def get_teacher_invitation_service() -> TeacherInvitationService:
    """Get the teacher invitation service singleton."""
    global _teacher_invitation_service
    if _teacher_invitation_service is None:
        _teacher_invitation_service = TeacherInvitationService()
    return _teacher_invitation_service
