"""Menu-driven WhatsApp bot — state machine dispatched by interactive-reply IDs.

Design choice: **no server-side session state.** The current position in
the menu is encoded in the ID of the button/list-row the user last tapped
— e.g. ``pick:balance:<child_id>`` means "user is answering the child
picker for the balance flow". This means we never touch Redis or a
sessions table for this bot; the state is fully in the message. When
Phase 2C adds AI mode we'll layer Redis on top for conversation memory,
but the menu bot doesn't need it.

Menu-ID vocabulary:

    menu:balance         Main menu → parent picked "Balance"
    menu:attendance      Main menu → parent picked "Attendance"
    menu:reports         Main menu → parent picked "Latest report"
    menu:teacher         Main menu → parent picked "Teacher contact"
    menu:announcements   Main menu → parent picked "Announcements"
    menu:help            Main menu → parent picked "Help"
    menu:main            Special: send the main menu again (e.g. from Back)

    pick:<flow>:<child>  Child picker → parent chose child for that flow
                          flow ∈ {balance, attendance, reports, teacher}

Anything else — plain text, unknown ID — resets to the main menu. This
"any input goes home" behaviour matches how most banking / utility
WhatsApp bots work and keeps stuck users unstuck.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ForbiddenException
from app.models import User
from app.services import whatsapp_bot_tools as tools
from app.utils.tenant_context import _current_user_id, _current_user_role, _tenant_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Response types — thin discriminated union
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TextReply:
    body: str


@dataclass(frozen=True)
class ButtonReply:
    body: str
    buttons: list[dict[str, str]]  # {id, title}
    header: str | None = None
    footer: str | None = None


@dataclass(frozen=True)
class ListReply:
    body: str
    button_text: str
    sections: list[dict[str, Any]]  # {title, rows: [{id, title, description}]}
    header: str | None = None
    footer: str | None = None


@dataclass(frozen=True)
class DocumentReply:
    """Send a PDF/DOCX/XLSX as an actual WhatsApp attachment.

    The parent sees a document bubble they can tap-to-open in WhatsApp —
    no browser round-trip needed. This is core to the "WhatsApp is the
    primary UI" product positioning: reports/invoices are consumed IN
    the chat, not by clicking through to the web app.

    ``file_bytes`` is uploaded to Meta's Media API first; the caller
    (webhook dispatcher) handles that + the send. ``caption`` may
    accompany the doc — usually a short "here's Sarah's report".
    """
    file_bytes: bytes
    mime_type: str
    filename: str
    caption: str | None = None


@dataclass(frozen=True)
class ImageReply:
    """Send a JPG/PNG as an actual WhatsApp image."""
    image_bytes: bytes
    mime_type: str
    caption: str | None = None


@dataclass(frozen=True)
class MultiReply:
    """Send multiple replies in sequence — e.g. a text intro + a PDF,
    or a caption + a batch of images. The dispatcher sends each in order.
    """
    parts: list["MenuResponse"]


MenuResponse = (
    TextReply | ButtonReply | ListReply
    | DocumentReply | ImageReply | MultiReply
)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def handle_menu_message(
    db: AsyncSession,
    user: User,
    text: str,
    interactive_id: str | None,
) -> MenuResponse:
    """Route one inbound WhatsApp message to the right handler.

    Sets the user's tenant + role context so nested tool calls that rely
    on ``get_tenant_id()`` (billing, attendance, etc.) work correctly.
    Context is torn down at exit so the webhook worker session isn't
    contaminated across messages.
    """
    tenant_tok = _tenant_id.set(user.tenant_id) if user.tenant_id else None
    user_tok = _current_user_id.set(user.id)
    role_tok = _current_user_role.set(user.role)
    try:
        return await _dispatch(db, user, text, interactive_id)
    finally:
        _current_user_role.reset(role_tok)
        _current_user_id.reset(user_tok)
        if tenant_tok is not None:
            _tenant_id.reset(tenant_tok)


async def _dispatch(
    db: AsyncSession,
    user: User,
    text: str,
    interactive_id: str | None,
) -> MenuResponse:
    key = (interactive_id or "").strip()

    if not key:
        return await _main_menu(db, user)

    if key == "menu:main":
        return await _main_menu(db, user)

    if key == "menu:help":
        return _help()

    if key == "menu:announcements":
        return await _announcements(db, user)

    if key.startswith("menu:"):
        flow = key[len("menu:"):]
        if flow in {"balance", "attendance", "reports", "teacher"}:
            return await _choose_child(db, user, flow)

    if key.startswith("pick:"):
        # pick:<flow>:<child_uuid>
        parts = key.split(":", 2)
        if len(parts) == 3:
            _, flow, child_id_raw = parts
            try:
                child_id = uuid.UUID(child_id_raw)
            except ValueError:
                return await _main_menu(db, user)
            return await _answer_for_child(db, user, flow, child_id)

    # Unknown / stale button ID — send them home.
    return await _main_menu(db, user)


# ---------------------------------------------------------------------------
# Menus
# ---------------------------------------------------------------------------

async def _main_menu(db: AsyncSession, user: User) -> MenuResponse:
    greeting = f"Hi {user.first_name}! 👋"
    body = (
        f"{greeting}\n\n"
        "What would you like to check?"
    )
    return ListReply(
        body=body,
        button_text="Open menu",
        header="ClassUp",
        footer="Reply with anything to see this menu again.",
        sections=[
            {
                "title": "Your child",
                "rows": [
                    {"id": "menu:balance",     "title": "Balance",        "description": "See outstanding invoices"},
                    {"id": "menu:attendance",  "title": "Attendance",     "description": "Last 7 days"},
                    {"id": "menu:reports",     "title": "Latest report",  "description": "Most recent finalised report"},
                    {"id": "menu:teacher",     "title": "Teacher contact","description": "Class teacher details"},
                ],
            },
            {
                "title": "School",
                "rows": [
                    {"id": "menu:announcements", "title": "Announcements", "description": "Recent school news"},
                    {"id": "menu:help",          "title": "Help",          "description": "What this bot can do"},
                ],
            },
        ],
    )


def _help() -> MenuResponse:
    return TextReply(
        body=(
            "*ClassUp on WhatsApp — what I can do*\n\n"
            "• Check your child's *balance* and unpaid invoices\n"
            "• Show recent *attendance*\n"
            "• Link you to the latest *report*\n"
            "• Show your child's *teacher* contact\n"
            "• List recent *school announcements*\n\n"
            "Send *menu* (or any message) to see the options.\n"
            "For anything else, log in at https://classup.co.za"
        )
    )


async def _choose_child(
    db: AsyncSession, user: User, flow: str
) -> MenuResponse:
    """After the parent picks a topic that needs a child, show a picker.

    If they only have one child, skip the picker and answer directly.
    """
    children = await tools.get_my_children(db, user.id)
    if not children:
        return TextReply(
            body=(
                "You don't have any children linked to your ClassUp account "
                "yet. Please contact the school to have your child added."
            )
        )
    if len(children) == 1:
        return await _answer_for_child(db, user, flow, children[0].id)

    prompt = {
        "balance":    "Whose balance would you like to see?",
        "attendance": "Whose attendance would you like to see?",
        "reports":    "Whose latest report would you like to see?",
        "teacher":    "Whose teacher would you like to contact?",
    }.get(flow, "Which child?")

    rows = [
        {
            "id": f"pick:{flow}:{c.id}",
            "title": c.first_name[:24],
            "description": (
                f"{c.class_name or 'No class'}"
                + (f" · {c.teacher_name}" if c.teacher_name else "")
            )[:72],
        }
        # WhatsApp caps at 10 rows per list. Schools with >10 kids at one
        # parent are unheard of but we clamp anyway.
        for c in children[:10]
    ]

    return ListReply(
        body=prompt,
        button_text="Pick child",
        sections=[{"title": "Your children", "rows": rows}],
        footer="Reply with anything to go back",
    )


# ---------------------------------------------------------------------------
# Per-flow answers
# ---------------------------------------------------------------------------

async def _answer_for_child(
    db: AsyncSession, user: User, flow: str, child_id: uuid.UUID
) -> MenuResponse:
    try:
        if flow == "balance":
            info = await tools.get_child_balance(db, user.id, child_id)
            return _format_balance(info)
        if flow == "attendance":
            info = await tools.get_child_attendance(db, user.id, child_id, days=7)
            return _format_attendance(info)
        if flow == "reports":
            info = await tools.get_child_latest_report(db, user.id, child_id)
            if info is None:
                child_name = await _child_first_name(db, user.id, child_id)
                return TextReply(
                    body=(
                        f"There aren't any finalised reports for "
                        f"{child_name} yet. Reply *menu* to go back."
                    )
                )
            return _format_report(info)
        if flow == "teacher":
            info = await tools.get_child_teacher(db, user.id, child_id)
            return _format_teacher(info)
    except ForbiddenException:
        # Someone tapped a stale menu button after being unlinked from the
        # child, or an ID from a different tenant. Silent fall-back to
        # main menu — don't leak the reason.
        logger.warning(
            "Parent %s tried to access child %s they don't own",
            user.id, child_id,
        )
        return await _main_menu(db, user)

    return await _main_menu(db, user)


async def _child_first_name(
    db: AsyncSession, parent_id: uuid.UUID, child_id: uuid.UUID
) -> str:
    """Fetch the child's first name for a friendlier empty-state message.

    Returns "your child" if lookup fails — never leaks a ForbiddenException
    here because we're only calling this when the parent has already been
    verified via the tool boundary a moment earlier.
    """
    from app.models import Student
    student = await db.get(Student, child_id)
    return student.first_name if student else "your child"


async def _announcements(db: AsyncSession, user: User) -> MenuResponse:
    items = await tools.get_recent_announcements(db, user.id, limit=5)
    if not items:
        return TextReply(
            body=(
                "No recent announcements from the school. Reply *menu* to "
                "go back."
            )
        )
    lines = ["*Recent announcements*\n"]
    for a in items:
        prefix = "📌 " if a.is_pinned else "• "
        scope = f" · {a.class_name}" if a.class_name else " · school-wide"
        date_str = a.created_at.strftime("%d %b")
        lines.append(f"{prefix}*{a.title}*  _{date_str}{scope}_")
        # Keep it terse for a WhatsApp reply — first 240 chars of body.
        snippet = (a.body or "").strip().replace("\n", " ")
        if snippet:
            lines.append(snippet[:240] + ("…" if len(snippet) > 240 else ""))
        lines.append("")

    lines.append("Reply *menu* to go back.")
    return TextReply(body="\n".join(lines))


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _format_balance(info: tools.BalanceInfo) -> MenuResponse:
    if info.total_outstanding <= Decimal("0"):
        return TextReply(
            body=(
                f"✅ *{info.child_name}* is fully paid up — nothing "
                "outstanding.\n\nReply *menu* to go back."
            )
        )

    lines = [
        f"*{info.child_name} — balance*",
        f"Total outstanding: *{info.currency} {info.total_outstanding:,.2f}*",
        "",
    ]
    for inv in info.unpaid_invoices[:5]:
        due = inv["due_date"].strftime("%d %b %Y") if inv.get("due_date") else "no due date"
        overdue = ""
        if inv["status"] == "OVERDUE":
            overdue = "  ⚠️ *overdue*"
        lines.append(
            f"• *{inv['number']}* — {info.currency} {inv['balance']:,.2f}  _(due {due}){overdue}_"
        )
    if len(info.unpaid_invoices) > 5:
        lines.append(f"…and {len(info.unpaid_invoices) - 5} more.")
    lines.append("")
    lines.append("Reply *menu* to go back.")
    return TextReply(body="\n".join(lines))


def _format_attendance(info: tools.AttendanceSummary) -> MenuResponse:
    if not info.records:
        return TextReply(
            body=(
                f"No attendance records for *{info.child_name}* in the "
                f"last {info.days_shown} days.\n\nReply *menu* to go back."
            )
        )
    icons = {
        "PRESENT":  "✅",
        "LATE":     "⏰",
        "ABSENT":   "❌",
        "EXCUSED":  "📝",
    }
    lines = [f"*{info.child_name} — last {info.days_shown} days*", ""]
    # Records come back with most recent first from the service; show them
    # that way — matches how people scan a WhatsApp reply.
    for r in info.records:
        icon = icons.get(r.status, "•")
        date_str = r.date.strftime("%a %d %b")
        line = f"{icon} {date_str} — *{r.status.title()}*"
        if r.check_in_time:
            line += f"  _(in {r.check_in_time.strftime('%H:%M')})_"
        lines.append(line)
        if r.notes:
            note = r.notes.strip().replace("\n", " ")[:120]
            lines.append(f"   _{note}_")
    lines.append("")
    lines.append("Reply *menu* to go back.")
    return TextReply(body="\n".join(lines))


def _format_report(info: tools.ReportSummary) -> MenuResponse:
    when = info.report_date.strftime("%A %d %B %Y")
    return TextReply(
        body=(
            f"📄 *Latest report for {info.child_name}*\n"
            f"For: {when}\n\n"
            f"View it here:\n{info.url}\n\n"
            "Reply *menu* to go back."
        )
    )


def _format_teacher(info: tools.TeacherInfo) -> MenuResponse:
    if info.teacher_name is None and info.class_name is None:
        return TextReply(
            body=(
                f"*{info.child_name}* isn't assigned to a class yet. "
                "Please contact the school for details.\n\n"
                "Reply *menu* to go back."
            )
        )
    if info.teacher_name is None:
        return TextReply(
            body=(
                f"*{info.child_name}* is in *{info.class_name}*, but no "
                "teacher has been assigned yet.\n\n"
                "Reply *menu* to go back."
            )
        )
    return TextReply(
        body=(
            f"👩‍🏫 *{info.child_name}'s teacher*\n"
            f"*{info.teacher_name}*\n"
            f"Class: {info.class_name or '(unassigned)'}\n\n"
            "To message the teacher directly, log in at "
            "https://classup.co.za and open Messages.\n\n"
            "Reply *menu* to go back."
        )
    )
