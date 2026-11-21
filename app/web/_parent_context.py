"""Parent-portal web helpers (Parent Portal iteration 1).

One place for:

* the parent's own children, in ONE canonical order (first name, last
  name, id) so the dashboard, fees, reports, attendance, timetable and
  the mobile drawer always list children the same way;
* the persisted child selection (``selected_child_id`` cookie). The
  cookie and any ``?child_id=`` / ``?student_id=`` query value are only
  a *preference*: every read is validated against the parent's own
  ``parent_students`` links. An unknown / foreign / malformed id falls
  back to the first own child and never widens access;
* the login redirect that keeps ``?next=`` for deep links;
* money formatting (``money`` Jinja filter) so every parent page shows
  "$1,234.00" the same way.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import ParentStudent, Student
from app.services.jurisdiction_service import CURRENCY_SYMBOLS
from app.templates_config import templates
from app.utils.tenant_context import (
    get_current_user_id_or_none,
    get_current_user_role,
    get_tenant_id,
)

SELECTED_CHILD_COOKIE = "selected_child_id"
_COOKIE_MAX_AGE = 60 * 60 * 24 * 180  # 180 days — a preference only


# ---------------------------------------------------------------------------
# Login redirect that survives deep links
# ---------------------------------------------------------------------------

def login_redirect(request: Request) -> RedirectResponse:
    """302 to /login carrying the current path + query as ``?next=``.

    ``next`` is always built from the request's own *path* (never a
    full URL), so it can only point back into this app.
    """
    next_url = request.url.path or "/dashboard"
    if request.url.query:
        next_url += "?" + request.url.query
    response = RedirectResponse(
        url=f"/login?next={quote(next_url, safe='/')}",
        status_code=302,
    )
    response.delete_cookie("access_token")
    return response


def require_login(request: Request) -> RedirectResponse | None:
    """Return a ``next=``-preserving login redirect when signed out."""
    if not get_current_user_id_or_none():
        return login_redirect(request)
    return None


def safe_next_path(raw: str | None, default: str = "/dashboard") -> str:
    """Only allow same-origin relative paths ("/x", never "//x" or "http:")."""
    if not raw or not raw.startswith("/") or "\\" in raw:
        return default
    # Browsers strip tabs/newlines and treat "/\t/evil" as "//evil", so
    # reject any whitespace or control character outright.
    if any(ord(ch) < 0x21 or ord(ch) == 0x7F for ch in raw):
        return default
    if raw.startswith("//"):
        return default
    return raw


# ---------------------------------------------------------------------------
# Children + persisted selection
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ParentChild:
    id: uuid.UUID
    first_name: str
    last_name: str
    class_id: uuid.UUID | None
    class_name: str | None

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def initials(self) -> str:
        return ((self.first_name[:1] or "") + (self.last_name[:1] or "")).upper()


def _sort_key(s: Any) -> tuple:
    return ((s.first_name or "").lower(), (s.last_name or "").lower(), str(s.id))


async def load_parent_children(
    db: AsyncSession, parent_id: uuid.UUID, tenant_id: uuid.UUID | None = None,
) -> list[ParentChild]:
    """The parent's own, non-deleted children at this tenant — canonical order."""
    tenant_id = tenant_id or get_tenant_id()
    rows = (await db.execute(
        select(Student)
        .join(ParentStudent, ParentStudent.student_id == Student.id)
        .where(
            ParentStudent.parent_id == parent_id,
            Student.tenant_id == tenant_id,
            Student.deleted_at.is_(None),
        )
        .options(selectinload(Student.school_class))
    )).scalars().unique().all()
    rows = sorted(rows, key=_sort_key)
    return [
        ParentChild(
            id=s.id,
            first_name=s.first_name or "",
            last_name=s.last_name or "",
            class_id=s.class_id,
            class_name=s.school_class.name if s.school_class else None,
        )
        for s in rows
    ]


def _parse_uuid(raw: Any) -> uuid.UUID | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, uuid.UUID):
        return raw
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError, AttributeError):
        return None


def pick_child(
    children: list[ParentChild], *candidates: Any,
) -> ParentChild | None:
    """First candidate id that is one of ``children``; else the first child.

    Candidates that aren't the parent's own child are silently ignored —
    a foreign id can never select (or reveal) another family's child.
    """
    if not children:
        return None
    by_id = {c.id: c for c in children}
    for raw in candidates:
        cid = _parse_uuid(raw)
        if cid is not None and cid in by_id:
            return by_id[cid]
    return children[0]


def requested_child_id(request: Request) -> str | None:
    q = request.query_params
    return q.get("child_id") or q.get("student_id")


async def get_parent_child_context(
    request: Request,
    db: AsyncSession,
    *,
    explicit: Any = None,
    prefer: Iterable[Any] = (),
) -> dict:
    """Children + validated selection for a PARENT viewer ({} otherwise).

    Priority: ``explicit`` (e.g. a path param) → ``?child_id`` /
    ``?student_id`` → ``prefer`` (page-specific fallbacks) → cookie →
    first child. Each is checked against the parent's own links.
    """
    if get_current_user_role() != "PARENT":
        return {}
    parent_id = get_current_user_id_or_none()
    if parent_id is None:
        return {}
    children = await load_parent_children(db, parent_id)
    selected = pick_child(
        children,
        explicit,
        requested_child_id(request),
        *list(prefer),
        request.cookies.get(SELECTED_CHILD_COOKIE),
    )
    return {
        "parent_children": children,
        "selected_child": selected,
        "selected_child_id": selected.id if selected else None,
    }


def remember_child(response, ctx: dict) -> None:
    """Persist the validated selection. Never stores an unvalidated id."""
    child = (ctx or {}).get("selected_child")
    if child is None or response is None:
        return
    try:
        response.set_cookie(
            SELECTED_CHILD_COOKIE,
            str(child.id),
            max_age=_COOKIE_MAX_AGE,
            httponly=False,  # read by the drawer to highlight the current child
            samesite="lax",
            path="/",
        )
    except Exception:
        pass


def is_own_child(ctx: dict, student_id: Any) -> bool:
    sid = _parse_uuid(student_id)
    return sid is not None and any(c.id == sid for c in ctx.get("parent_children", []))


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------

def currency_symbol(code: str | None) -> str:
    code = (code or "USD").upper()
    return CURRENCY_SYMBOLS.get(code, code + " ")


def money(value: Any, currency: str | None = "USD") -> str:
    """"$1,234.00" (negative → "-$1,234.00"). Accepts code or symbol."""
    try:
        amount = Decimal(str(value if value is not None else 0))
    except (InvalidOperation, ValueError):
        return str(value)
    cur = currency or "USD"
    sym = currency_symbol(cur) if (cur.isalpha() and len(cur) == 3) else cur
    sign = "-" if amount < 0 else ""
    return f"{sign}{sym}{abs(amount):,.2f}"


templates.env.filters.setdefault("money", money)
templates.env.globals.setdefault("currency_symbol", currency_symbol)
