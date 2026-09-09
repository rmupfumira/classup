"""Tests for the menu-driven WhatsApp bot (Phase 2B).

Three layers to cover:

  1. **Tool authorization** — a parent cannot see another parent's child's
     data. This is the tool-level guarantee; the menu just calls the tools.

  2. **State machine dispatch** — the right handler runs for each
     interactive-reply ID, and plain text sends the main menu.

  3. **Interactive-reply parsing** — the webhook parser now surfaces the
     button/list-row ID, not just its title. The state machine depends
     on this to know which button was tapped.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ForbiddenException
from app.models import (
    ParentStudent,
    SchoolClass,
    Student,
    TeacherClass,
    Tenant,
    User,
)
from app.models.user import Role
from app.services import whatsapp_bot_tools as tools
from app.services import whatsapp_menu_bot as bot
from app.services.whatsapp_service import WhatsAppService
from app.utils.security import hash_password
from app.utils.tenant_context import _tenant_id


# ---------------------------------------------------------------------------
# Fixtures — build a tenant with one parent + one child + one class + teacher
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def scenario(db: AsyncSession):
    """A minimal working tenant graph: parent -> child -> class -> teacher.

    Also creates a *second* parent + child in the SAME tenant so the
    ownership checks have something adversarial to test against.
    """
    tenant_id = uuid.uuid4()
    slug = f"wbmenu-{tenant_id.hex[:8]}"
    tenant = Tenant(
        id=tenant_id,
        name=f"Menu Bot Test {tenant_id.hex[:6]}",
        slug=slug,
        email=f"admin@{slug}.test",
        education_type="PRIMARY_SCHOOL",
        settings={"features": {"whatsapp_enabled": True}, "education_type": "PRIMARY_SCHOOL"},
        is_active=True,
        onboarding_completed=True,
    )
    db.add(tenant)
    await db.flush()

    teacher = User(
        id=uuid.uuid4(), tenant_id=tenant_id,
        email=f"teach-{tenant_id.hex[:6]}@t.test",
        password_hash=hash_password("x"),
        first_name="Grace", last_name="Ndlovu",
        role=Role.TEACHER.value, is_active=True,
    )
    parent = User(
        id=uuid.uuid4(), tenant_id=tenant_id,
        email=f"parent-{tenant_id.hex[:6]}@p.test",
        password_hash=hash_password("x"),
        first_name="Amara", last_name="Moyo",
        role=Role.PARENT.value, is_active=True,
    )
    other_parent = User(
        id=uuid.uuid4(), tenant_id=tenant_id,
        email=f"other-{tenant_id.hex[:6]}@p.test",
        password_hash=hash_password("x"),
        first_name="Sipho", last_name="Dube",
        role=Role.PARENT.value, is_active=True,
    )
    db.add_all([teacher, parent, other_parent])
    await db.flush()

    school_class = SchoolClass(
        id=uuid.uuid4(), tenant_id=tenant_id,
        name="Grade 3A", is_active=True,
    )
    db.add(school_class)
    await db.flush()

    db.add(TeacherClass(
        id=uuid.uuid4(), teacher_id=teacher.id,
        class_id=school_class.id, is_primary=True,
    ))

    child = Student(
        id=uuid.uuid4(), tenant_id=tenant_id,
        first_name="Zaria", last_name="Moyo",
        class_id=school_class.id, is_active=True,
    )
    other_child = Student(
        id=uuid.uuid4(), tenant_id=tenant_id,
        first_name="Themba", last_name="Dube",
        class_id=school_class.id, is_active=True,
    )
    db.add_all([child, other_child])
    await db.flush()

    db.add(ParentStudent(
        id=uuid.uuid4(), parent_id=parent.id, student_id=child.id,
        relationship_type="PARENT", is_primary=True,
    ))
    db.add(ParentStudent(
        id=uuid.uuid4(), parent_id=other_parent.id, student_id=other_child.id,
        relationship_type="PARENT", is_primary=True,
    ))
    await db.commit()

    tenant_tok = _tenant_id.set(tenant_id)
    try:
        yield SimpleNamespace(
            tenant=tenant, parent=parent, other_parent=other_parent,
            teacher=teacher, school_class=school_class,
            child=child, other_child=other_child,
        )
    finally:
        _tenant_id.reset(tenant_tok)
        # Raw DELETE in a fresh session — sidesteps the ORM's default
        # "nullify child FKs before delete" behaviour and lets Postgres
        # cascade-delete via the ON DELETE CASCADE constraints instead.
        from app.database import get_db_context
        from sqlalchemy import text as sql_text
        async with get_db_context() as db2:
            await db2.execute(
                sql_text("DELETE FROM tenants WHERE id = :tid"),
                {"tid": tenant_id},
            )
            await db2.commit()


# ---------------------------------------------------------------------------
# Tool authorization
# ---------------------------------------------------------------------------

class TestToolAuthorization:
    async def test_get_child_balance_blocks_other_parents_child(
        self, db: AsyncSession, scenario
    ):
        # Parent A tries to pull Parent B's child. Must raise.
        with pytest.raises(ForbiddenException):
            await tools.get_child_balance(
                db, scenario.parent.id, scenario.other_child.id
            )

    async def test_get_child_attendance_blocks_other_parents_child(
        self, db: AsyncSession, scenario
    ):
        with pytest.raises(ForbiddenException):
            await tools.get_child_attendance(
                db, scenario.parent.id, scenario.other_child.id
            )

    async def test_get_child_teacher_blocks_other_parents_child(
        self, db: AsyncSession, scenario
    ):
        with pytest.raises(ForbiddenException):
            await tools.get_child_teacher(
                db, scenario.parent.id, scenario.other_child.id
            )

    async def test_get_child_latest_report_blocks_other_parents_child(
        self, db: AsyncSession, scenario
    ):
        with pytest.raises(ForbiddenException):
            await tools.get_child_latest_report(
                db, scenario.parent.id, scenario.other_child.id
            )

    async def test_get_my_children_returns_only_own(
        self, db: AsyncSession, scenario
    ):
        # No arbitrary-child parameter, but confirm the query itself
        # doesn't leak Parent B's child to Parent A.
        children = await tools.get_my_children(db, scenario.parent.id)
        ids = {c.id for c in children}
        assert scenario.child.id in ids
        assert scenario.other_child.id not in ids

    async def test_get_child_teacher_returns_primary(
        self, db: AsyncSession, scenario
    ):
        info = await tools.get_child_teacher(
            db, scenario.parent.id, scenario.child.id
        )
        assert info.teacher_name == "Grace Ndlovu"
        assert info.class_name == "Grade 3A"


# ---------------------------------------------------------------------------
# State machine dispatch
# ---------------------------------------------------------------------------

class TestStateMachine:
    """Handler routing — the dispatch table is the whole spec here."""

    async def test_plain_text_shows_main_menu(
        self, db: AsyncSession, scenario
    ):
        reply = await bot.handle_menu_message(
            db=db, user=scenario.parent, text="hello", interactive_id=None,
        )
        assert isinstance(reply, bot.ListReply)
        # Menu must include the main-flow options as list row IDs.
        row_ids = {r["id"] for s in reply.sections for r in s["rows"]}
        assert {"menu:balance", "menu:attendance", "menu:reports",
                "menu:teacher", "menu:announcements", "menu:help"} <= row_ids

    async def test_help_returns_text(self, db: AsyncSession, scenario):
        reply = await bot.handle_menu_message(
            db=db, user=scenario.parent, text="", interactive_id="menu:help",
        )
        assert isinstance(reply, bot.TextReply)
        # Lower-case the whole thing to be case-insensitive.
        assert "what i can do" in reply.body.lower()

    async def test_single_child_skips_picker(
        self, db: AsyncSession, scenario
    ):
        # Parent has exactly one child → tapping "balance" should skip the
        # picker step and answer immediately.
        reply = await bot.handle_menu_message(
            db=db, user=scenario.parent, text="",
            interactive_id="menu:balance",
        )
        assert isinstance(reply, bot.TextReply)
        # No unpaid invoices in this scenario → "fully paid up".
        assert "fully paid" in reply.body.lower()
        assert scenario.child.first_name in reply.body

    async def test_teacher_flow_from_menu(
        self, db: AsyncSession, scenario
    ):
        reply = await bot.handle_menu_message(
            db=db, user=scenario.parent, text="",
            interactive_id="menu:teacher",
        )
        assert isinstance(reply, bot.TextReply)
        assert "Grace Ndlovu" in reply.body
        assert "Grade 3A" in reply.body

    async def test_stale_button_id_falls_back_to_menu(
        self, db: AsyncSession, scenario
    ):
        reply = await bot.handle_menu_message(
            db=db, user=scenario.parent, text="",
            interactive_id="menu:nonsense",
        )
        # Unknown menu key → main menu.
        assert isinstance(reply, bot.ListReply)

    async def test_pick_with_bad_uuid_falls_back_to_menu(
        self, db: AsyncSession, scenario
    ):
        reply = await bot.handle_menu_message(
            db=db, user=scenario.parent, text="",
            interactive_id="pick:balance:not-a-uuid",
        )
        assert isinstance(reply, bot.ListReply)

    async def test_pick_stale_child_falls_back_silently(
        self, db: AsyncSession, scenario
    ):
        # Parent taps a button carrying another parent's child_id (stale
        # menu, or a copy-pasted ID from a different session). Must NOT
        # raise; must fall back to the main menu without leaking.
        reply = await bot.handle_menu_message(
            db=db, user=scenario.parent, text="",
            interactive_id=f"pick:balance:{scenario.other_child.id}",
        )
        assert isinstance(reply, bot.ListReply)


# ---------------------------------------------------------------------------
# Parser — interactive_id must survive the round-trip
# ---------------------------------------------------------------------------

class TestParserInteractiveId:
    def _service(self) -> WhatsAppService:
        return WhatsAppService()

    def test_button_reply_extracts_id(self):
        payload = {
            "entry": [{"changes": [{"value": {"messages": [{
                "from": "27821234567",
                "id": "wamid.X",
                "type": "interactive",
                "interactive": {
                    "type": "button_reply",
                    "button_reply": {"id": "menu:balance", "title": "Balance"},
                },
                "timestamp": "1700000000",
            }]}}]}]
        }
        msgs = self._service().parse_webhook_message(payload)
        assert len(msgs) == 1
        assert msgs[0]["text"] == "Balance"
        assert msgs[0]["interactive_id"] == "menu:balance"

    def test_list_reply_extracts_id(self):
        payload = {
            "entry": [{"changes": [{"value": {"messages": [{
                "from": "27821234567",
                "id": "wamid.Y",
                "type": "interactive",
                "interactive": {
                    "type": "list_reply",
                    "list_reply": {
                        "id": "pick:balance:abc-def",
                        "title": "Zaria",
                        "description": "Grade 3A",
                    },
                },
                "timestamp": "1700000000",
            }]}}]}]
        }
        msgs = self._service().parse_webhook_message(payload)
        assert msgs[0]["interactive_id"] == "pick:balance:abc-def"
        assert msgs[0]["text"] == "Zaria"

    def test_plain_text_has_null_interactive_id(self):
        payload = {
            "entry": [{"changes": [{"value": {"messages": [{
                "from": "27821234567",
                "id": "wamid.Z",
                "type": "text",
                "text": {"body": "hello"},
                "timestamp": "1700000000",
            }]}}]}]
        }
        msgs = self._service().parse_webhook_message(payload)
        assert msgs[0]["text"] == "hello"
        assert msgs[0]["interactive_id"] is None


# ---------------------------------------------------------------------------
# Formatting sanity — a handful of properties the format helpers must hold
# ---------------------------------------------------------------------------

class TestFormatters:
    def test_balance_zero_says_fully_paid(self):
        info = tools.BalanceInfo(
            child_name="Zaria",
            total_outstanding=Decimal("0"),
            currency="ZAR",
            unpaid_invoices=[],
        )
        reply = bot._format_balance(info)
        assert isinstance(reply, bot.TextReply)
        assert "fully paid" in reply.body.lower()

    def test_balance_lists_first_five_invoices(self):
        info = tools.BalanceInfo(
            child_name="Zaria",
            total_outstanding=Decimal("6000.00"),
            currency="ZAR",
            unpaid_invoices=[
                {"number": f"INV-2026-{i:04d}", "due_date": date(2026, 3, 1),
                 "balance": Decimal("1000.00"), "status": "SENT"}
                for i in range(7)
            ],
        )
        reply = bot._format_balance(info)
        assert "INV-2026-0000" in reply.body
        assert "INV-2026-0004" in reply.body
        # Only 5 shown; 6/7 truncated.
        assert "and 2 more" in reply.body

    def test_attendance_uses_status_icons(self):
        info = tools.AttendanceSummary(
            child_name="Zaria", days_shown=3,
            records=[
                tools.AttendanceDay(date=date(2026, 3, 1), status="PRESENT",
                                     check_in_time=None, notes=None),
                tools.AttendanceDay(date=date(2026, 3, 2), status="ABSENT",
                                     check_in_time=None, notes="Sick"),
            ],
        )
        reply = bot._format_attendance(info)
        assert "✅" in reply.body
        assert "❌" in reply.body
        assert "Sick" in reply.body
