"""Global search — one API endpoint, role-scoped queries.

Powers the navbar search box. Each role sees only what they're
allowed to see elsewhere in the app; the permission matrix here
mirrors the one in CLAUDE.md so we never leak a row that would 404
on the detail page. Queries are intentionally shallow (ILIKE on the
columns a user would actually recognise — names, numbers) so a
single typed query is one round-trip and feels instant.

Groups returned per role:
  PARENT       → Children (own only)
  TEACHER      → Classes (own), Students (in own classes)
  SCHOOL_ADMIN → Students, Classes, Teachers, Parents, Invoices
  SUPER_ADMIN  → Tenants, Admins

Each item carries {id, title, subtitle, url, icon} — ``icon`` is a
short key the client maps to an SVG so the API stays JSON-only.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import BillingInvoice
from app.models.school_class import SchoolClass, TeacherClass
from app.models.student import ParentStudent, Student
from app.models.tenant import Tenant
from app.models.user import User


@dataclass(frozen=True)
class SearchItem:
    """A single result row inside a group."""

    id: str
    title: str
    subtitle: str
    url: str
    icon: str


@dataclass(frozen=True)
class SearchGroup:
    """A named list of SearchItems of one kind (Students, Classes, …)."""

    type: str
    label: str
    items: list[SearchItem]


def _group_dict(g: SearchGroup) -> dict:
    """SearchGroup → dict ready for APIResponse JSON."""
    return {
        "type": g.type,
        "label": g.label,
        "items": [asdict(i) for i in g.items],
    }


def _escape_ilike(q: str) -> str:
    """Make a user's query safe inside an ILIKE pattern.

    SQLAlchemy already parameterises the value so there's no SQL
    injection risk, but a user who types ``50%`` or ``_test_`` would
    otherwise accidentally match everything. Escape the two ILIKE
    wildcards so they only match themselves.
    """
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class SearchService:
    """One search method per role; the API layer dispatches on role."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _name_filter(like: str, *, email_col=None, number_col=None):
        """Build a (first OR last OR email OR number) ILIKE filter.

        Email and invoice-number columns are optional; pass them when
        they exist on the target model so the typed query also matches
        an email address or ``INV-2026-0042``.
        """
        clauses = [
            User.first_name.ilike(like, escape="\\"),
            User.last_name.ilike(like, escape="\\"),
            # Jinja users often type the full name ("Jane Doe") — match
            # the concatenation too so the typeahead doesn't silently
            # drop that natural query.
            func.concat(User.first_name, " ", User.last_name).ilike(like, escape="\\"),
        ]
        if email_col is not None:
            clauses.append(email_col.ilike(like, escape="\\"))
        if number_col is not None:
            clauses.append(number_col.ilike(like, escape="\\"))
        return or_(*clauses)

    # ------------------------------------------------------------------
    # Role dispatch
    # ------------------------------------------------------------------
    async def search(
        self,
        db: AsyncSession,
        *,
        q: str,
        role: str,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID | None,
        limit_per_group: int = 5,
    ) -> dict:
        """Run the role-appropriate search and return the dict payload."""
        q = (q or "").strip()
        if len(q) < 2:
            # Two-character minimum keeps the server quiet when the
            # user types their first letter — the frontend already
            # debounces, so this is belt-and-braces.
            return {"query": q, "groups": [], "total": 0}

        like = f"%{_escape_ilike(q)}%"

        if role == "SUPER_ADMIN":
            groups = await self._search_super_admin(db, like, limit_per_group)
        elif role == "SCHOOL_ADMIN":
            groups = await self._search_school_admin(
                db, like, tenant_id, limit_per_group,
            )
        elif role == "TEACHER":
            groups = await self._search_teacher(
                db, like, tenant_id, user_id, limit_per_group,
            )
        elif role == "PARENT":
            groups = await self._search_parent(
                db, like, tenant_id, user_id, limit_per_group,
            )
        else:
            groups = []

        groups = [g for g in groups if g.items]
        total = sum(len(g.items) for g in groups)
        return {
            "query": q,
            "groups": [_group_dict(g) for g in groups],
            "total": total,
        }

    # ------------------------------------------------------------------
    # Per-role searches
    # ------------------------------------------------------------------
    async def _search_super_admin(
        self, db: AsyncSession, like: str, limit: int,
    ) -> list[SearchGroup]:
        """Platform-level: tenants + admin users across all tenants."""
        tenants_q = (
            select(Tenant)
            .where(
                Tenant.deleted_at.is_(None),
                or_(
                    Tenant.name.ilike(like, escape="\\"),
                    Tenant.slug.ilike(like, escape="\\"),
                    Tenant.email.ilike(like, escape="\\"),
                ),
            )
            .order_by(Tenant.name)
            .limit(limit)
        )
        tenant_rows = (await db.execute(tenants_q)).scalars().all()
        tenant_items = [
            SearchItem(
                id=str(t.id),
                title=t.name,
                subtitle=t.slug,
                url=f"/admin/tenants/{t.id}",
                icon="tenant",
            )
            for t in tenant_rows
        ]

        admins_q = (
            select(User)
            .where(
                User.deleted_at.is_(None),
                User.role.in_(["SCHOOL_ADMIN", "SUPER_ADMIN"]),
                self._name_filter(like, email_col=User.email),
            )
            .order_by(User.last_name, User.first_name)
            .limit(limit)
        )
        admin_rows = (await db.execute(admins_q)).scalars().all()
        admin_items = [
            SearchItem(
                id=str(u.id),
                title=f"{u.first_name} {u.last_name}",
                subtitle=u.email,
                url=f"/admin/users/{u.id}",
                icon="admin",
            )
            for u in admin_rows
        ]
        return [
            SearchGroup(type="tenants", label="Tenants", items=tenant_items),
            SearchGroup(type="admins", label="Admins", items=admin_items),
        ]

    async def _search_school_admin(
        self,
        db: AsyncSession,
        like: str,
        tenant_id: uuid.UUID | None,
        limit: int,
    ) -> list[SearchGroup]:
        """School-wide: students, classes, teachers, parents, invoices."""
        if tenant_id is None:
            return []

        groups: list[SearchGroup] = []

        # Students
        students_q = (
            select(Student)
            .where(
                Student.tenant_id == tenant_id,
                Student.deleted_at.is_(None),
                or_(
                    Student.first_name.ilike(like, escape="\\"),
                    Student.last_name.ilike(like, escape="\\"),
                    func.concat(
                        Student.first_name, " ", Student.last_name,
                    ).ilike(like, escape="\\"),
                ),
            )
            .order_by(Student.last_name, Student.first_name)
            .limit(limit)
        )
        student_rows = (await db.execute(students_q)).scalars().all()
        groups.append(SearchGroup(
            type="students", label="Students",
            items=[
                SearchItem(
                    id=str(s.id),
                    title=f"{s.first_name} {s.last_name}",
                    subtitle=(s.school_class.name if s.school_class else "Unassigned"),
                    url=f"/students/{s.id}",
                    icon="student",
                )
                for s in student_rows
            ],
        ))

        # Classes
        classes_q = (
            select(SchoolClass)
            .where(
                SchoolClass.tenant_id == tenant_id,
                SchoolClass.deleted_at.is_(None),
                SchoolClass.name.ilike(like, escape="\\"),
            )
            .order_by(SchoolClass.name)
            .limit(limit)
        )
        class_rows = (await db.execute(classes_q)).scalars().all()
        groups.append(SearchGroup(
            type="classes", label="Classes",
            items=[
                SearchItem(
                    id=str(c.id),
                    title=c.name,
                    subtitle=(c.grade_level or c.age_group or ""),
                    url=f"/classes/{c.id}",
                    icon="class",
                )
                for c in class_rows
            ],
        ))

        # Teachers
        teachers_q = (
            select(User)
            .where(
                User.tenant_id == tenant_id,
                User.deleted_at.is_(None),
                User.role == "TEACHER",
                self._name_filter(like, email_col=User.email),
            )
            .order_by(User.last_name, User.first_name)
            .limit(limit)
        )
        teacher_rows = (await db.execute(teachers_q)).scalars().all()
        groups.append(SearchGroup(
            type="teachers", label="Teachers",
            items=[
                SearchItem(
                    id=str(u.id),
                    title=f"{u.first_name} {u.last_name}",
                    subtitle=u.email,
                    url=f"/teachers/{u.id}",
                    icon="teacher",
                )
                for u in teacher_rows
            ],
        ))

        # Parents
        parents_q = (
            select(User)
            .where(
                User.tenant_id == tenant_id,
                User.deleted_at.is_(None),
                User.role == "PARENT",
                self._name_filter(like, email_col=User.email),
            )
            .order_by(User.last_name, User.first_name)
            .limit(limit)
        )
        parent_rows = (await db.execute(parents_q)).scalars().all()
        groups.append(SearchGroup(
            type="parents", label="Parents",
            items=[
                SearchItem(
                    id=str(u.id),
                    title=f"{u.first_name} {u.last_name}",
                    subtitle=u.email,
                    url=f"/parents/{u.id}",
                    icon="parent",
                )
                for u in parent_rows
            ],
        ))

        # Invoices — match on invoice number or linked student's name.
        inv_q = (
            select(BillingInvoice)
            .join(Student, Student.id == BillingInvoice.student_id)
            .where(
                BillingInvoice.tenant_id == tenant_id,
                BillingInvoice.deleted_at.is_(None),
                or_(
                    BillingInvoice.invoice_number.ilike(like, escape="\\"),
                    Student.first_name.ilike(like, escape="\\"),
                    Student.last_name.ilike(like, escape="\\"),
                ),
            )
            .order_by(BillingInvoice.created_at.desc())
            .limit(limit)
        )
        inv_rows = (await db.execute(inv_q)).scalars().all()
        groups.append(SearchGroup(
            type="invoices", label="Invoices",
            items=[
                SearchItem(
                    id=str(inv.id),
                    title=inv.invoice_number,
                    subtitle=(
                        f"{inv.student.first_name} {inv.student.last_name} · "
                        f"{inv.status}"
                        if inv.student else inv.status
                    ),
                    url=f"/billing/invoices/{inv.id}",
                    icon="invoice",
                )
                for inv in inv_rows
            ],
        ))

        return groups

    async def _search_teacher(
        self,
        db: AsyncSession,
        like: str,
        tenant_id: uuid.UUID | None,
        user_id: uuid.UUID,
        limit: int,
    ) -> list[SearchGroup]:
        """Teachers see only their own classes and students in them."""
        if tenant_id is None:
            return []

        # Teacher's own class ids — reused across both queries.
        class_ids_q = select(TeacherClass.class_id).where(
            TeacherClass.teacher_id == user_id,
        )

        classes_q = (
            select(SchoolClass)
            .where(
                SchoolClass.tenant_id == tenant_id,
                SchoolClass.deleted_at.is_(None),
                SchoolClass.id.in_(class_ids_q),
                SchoolClass.name.ilike(like, escape="\\"),
            )
            .order_by(SchoolClass.name)
            .limit(limit)
        )
        class_rows = (await db.execute(classes_q)).scalars().all()

        students_q = (
            select(Student)
            .where(
                Student.tenant_id == tenant_id,
                Student.deleted_at.is_(None),
                Student.class_id.in_(class_ids_q),
                or_(
                    Student.first_name.ilike(like, escape="\\"),
                    Student.last_name.ilike(like, escape="\\"),
                    func.concat(
                        Student.first_name, " ", Student.last_name,
                    ).ilike(like, escape="\\"),
                ),
            )
            .order_by(Student.last_name, Student.first_name)
            .limit(limit)
        )
        student_rows = (await db.execute(students_q)).scalars().all()

        return [
            SearchGroup(
                type="classes", label="My Classes",
                items=[
                    SearchItem(
                        id=str(c.id),
                        title=c.name,
                        subtitle=(c.grade_level or c.age_group or ""),
                        url=f"/classes/{c.id}",
                        icon="class",
                    )
                    for c in class_rows
                ],
            ),
            SearchGroup(
                type="students", label="Students",
                items=[
                    SearchItem(
                        id=str(s.id),
                        title=f"{s.first_name} {s.last_name}",
                        subtitle=(
                            s.school_class.name if s.school_class else ""
                        ),
                        url=f"/students/{s.id}",
                        icon="student",
                    )
                    for s in student_rows
                ],
            ),
        ]

    async def _search_parent(
        self,
        db: AsyncSession,
        like: str,
        tenant_id: uuid.UUID | None,
        user_id: uuid.UUID,
        limit: int,
    ) -> list[SearchGroup]:
        """Parents see only their own children."""
        if tenant_id is None:
            return []

        child_ids_q = select(ParentStudent.student_id).where(
            ParentStudent.parent_id == user_id,
        )
        students_q = (
            select(Student)
            .where(
                Student.tenant_id == tenant_id,
                Student.deleted_at.is_(None),
                Student.id.in_(child_ids_q),
                or_(
                    Student.first_name.ilike(like, escape="\\"),
                    Student.last_name.ilike(like, escape="\\"),
                    func.concat(
                        Student.first_name, " ", Student.last_name,
                    ).ilike(like, escape="\\"),
                ),
            )
            .order_by(Student.last_name, Student.first_name)
            .limit(limit)
        )
        student_rows = (await db.execute(students_q)).scalars().all()
        return [
            SearchGroup(
                type="children", label="My Children",
                items=[
                    SearchItem(
                        id=str(s.id),
                        title=f"{s.first_name} {s.last_name}",
                        subtitle=(
                            s.school_class.name if s.school_class else ""
                        ),
                        url=f"/students/{s.id}",
                        icon="student",
                    )
                    for s in student_rows
                ],
            ),
        ]


_service = SearchService()


def get_search_service() -> SearchService:
    return _service
