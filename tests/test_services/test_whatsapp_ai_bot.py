"""Tests for the AI-mode WhatsApp bot (Phase 2C).

We don't hit the real Anthropic API — those are billed and non-
deterministic. Instead we mock ``AsyncAnthropic.messages.create`` with
scripted responses that exercise:

  1. **Config storage** — MASKED round-trip preserves the API key when
     the admin edits the model without re-entering the key.

  2. **Tool schema** — the six tools all appear, with the parent_id
     omitted from every schema (that's how we enforce tenant isolation
     — Claude cannot pass one).

  3. **Tool executor** — malformed input (bad UUID) surfaces as a
     structured error to Claude instead of crashing.

  4. **AI loop** — the loop handles a straight text reply, a single
     tool-use round-trip, and falls back to the menu bot when
     Anthropic throws.

  5. **Fallback** — no API key = degrade to menu bot; Anthropic error =
     degrade to menu bot; both are silent to the parent.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ParentStudent, SchoolClass, Student, SystemSettings,
    Tenant, User,
)
from app.models.user import Role
from app.services import ai_config, whatsapp_ai_bot
from app.services.ai_config import AIConfig, MASKED
from app.services.whatsapp_menu_bot import TextReply
from app.utils.security import hash_password
from app.utils.tenant_context import _tenant_id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(autouse=True)
async def _reset_ai_config(db: AsyncSession):
    """Ensure the ai_config system_settings row is clean for every test."""
    await db.execute(
        SystemSettings.__table__.delete().where(
            SystemSettings.key == "ai_config"
        )
    )
    await db.commit()
    yield


@pytest_asyncio.fixture
async def parent_with_child(db: AsyncSession):
    """A parent with one child in a class — minimum viable graph for
    exercising the tool executor + AI loop."""
    tenant_id = uuid.uuid4()
    slug = f"aitest-{tenant_id.hex[:8]}"
    tenant = Tenant(
        id=tenant_id,
        name=f"AI Test {tenant_id.hex[:6]}",
        slug=slug,
        email=f"admin@{slug}.test",
        education_type="PRIMARY_SCHOOL",
        settings={"features": {"whatsapp_enabled": True, "whatsapp_ai_enabled": True}},
        is_active=True,
        onboarding_completed=True,
    )
    db.add(tenant)
    await db.flush()

    parent = User(
        id=uuid.uuid4(), tenant_id=tenant_id,
        email=f"parent-{tenant_id.hex[:6]}@t.test",
        password_hash=hash_password("x"),
        first_name="Nomsa", last_name="Khumalo",
        role=Role.PARENT.value, is_active=True,
    )
    db.add(parent)
    await db.flush()

    school_class = SchoolClass(
        id=uuid.uuid4(), tenant_id=tenant_id, name="Grade 2B", is_active=True,
    )
    db.add(school_class)
    await db.flush()

    child = Student(
        id=uuid.uuid4(), tenant_id=tenant_id,
        first_name="Sipho", last_name="Khumalo",
        class_id=school_class.id, is_active=True,
    )
    db.add(child)
    await db.flush()

    db.add(ParentStudent(
        id=uuid.uuid4(), parent_id=parent.id, student_id=child.id,
        relationship_type="PARENT", is_primary=True,
    ))
    await db.commit()

    tok = _tenant_id.set(tenant_id)
    try:
        yield SimpleNamespace(
            tenant=tenant, parent=parent, child=child, school_class=school_class,
        )
    finally:
        _tenant_id.reset(tok)
        from app.database import get_db_context
        from sqlalchemy import text as sql_text
        async with get_db_context() as db2:
            await db2.execute(
                sql_text("DELETE FROM tenants WHERE id = :tid"),
                {"tid": tenant_id},
            )
            await db2.commit()


# ---------------------------------------------------------------------------
# Config storage
# ---------------------------------------------------------------------------

class TestAIConfig:
    async def test_get_falls_back_to_env(self, db: AsyncSession, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-abc")
        cfg = await ai_config.get_config(db)
        assert cfg.api_key == "sk-ant-env-abc"
        # Default model kicks in when nothing configured.
        assert cfg.model == "claude-haiku-4-5"
        assert cfg.configured is True

    async def test_save_persists_and_configured_flips(self, db: AsyncSession):
        await ai_config.save_config(db, {
            "api_key": "sk-ant-live-xyz",
            "model": "claude-haiku-4-5",
        })
        await db.commit()

        cfg = await ai_config.get_config(db)
        assert cfg.api_key == "sk-ant-live-xyz"
        assert cfg.configured is True

    async def test_masked_round_trip_preserves_key(self, db: AsyncSession):
        # First save with real values.
        await ai_config.save_config(db, {
            "api_key": "sk-ant-secret",
            "model": "claude-haiku-4-5",
            "daily_message_cap_per_user": 100,
        })
        await db.commit()

        # Second save uses MASKED for the key — model changes, key stays.
        await ai_config.save_config(db, {
            "api_key": MASKED,
            "model": "claude-sonnet-5",
            "daily_message_cap_per_user": 50,
        })
        await db.commit()

        cfg = await ai_config.get_config(db)
        assert cfg.api_key == "sk-ant-secret"     # preserved
        assert cfg.model == "claude-sonnet-5"     # updated
        assert cfg.daily_message_cap_per_user == 50

    async def test_with_masked_secrets_hides_only_api_key(
        self, db: AsyncSession
    ):
        await ai_config.save_config(db, {
            "api_key": "sk-ant-secret",
            "model": "claude-haiku-4-5",
        })
        await db.commit()

        cfg = await ai_config.get_config(db)
        masked = cfg.with_masked_secrets()
        assert masked["api_key"] == MASKED
        # Model isn't a secret — visible.
        assert masked["model"] == "claude-haiku-4-5"
        assert masked["configured"] is True


# ---------------------------------------------------------------------------
# Tool schema — the contract Claude sees
# ---------------------------------------------------------------------------

class TestToolSchemas:
    def test_all_six_tools_present(self):
        schemas = whatsapp_ai_bot._tool_schemas()
        names = {s["name"] for s in schemas}
        assert names == {
            "get_my_children",
            "get_child_balance",
            "get_child_attendance",
            "get_child_latest_report",
            "get_child_teacher",
            "get_recent_announcements",
        }

    def test_parent_id_never_in_schema(self):
        """The whole tenant-isolation story rests on Claude NEVER being
        able to pass a parent_id — every tool binds it from context. If
        this test fails someone leaked parent_id into a public schema."""
        schemas = whatsapp_ai_bot._tool_schemas()
        for s in schemas:
            props = s["input_schema"].get("properties", {})
            assert "parent_id" not in props, (
                f"parent_id must NOT be in the {s['name']} tool schema"
            )
            required = s["input_schema"].get("required", [])
            assert "parent_id" not in required


# ---------------------------------------------------------------------------
# Tool executor
# ---------------------------------------------------------------------------

class TestToolExecutor:
    async def test_run_tool_bad_uuid_returns_error(
        self, db: AsyncSession, parent_with_child
    ):
        result = await whatsapp_ai_bot._run_tool(
            db=db, parent_id=parent_with_child.parent.id,
            name="get_child_balance",
            tool_input={"child_id": "not-a-real-uuid"},
        )
        assert isinstance(result, dict)
        assert result["error"] == "bad_input"

    async def test_run_tool_stranger_child_returns_not_allowed(
        self, db: AsyncSession, parent_with_child
    ):
        # A random UUID Claude might hallucinate — the tool layer rejects.
        stranger_id = uuid.uuid4()
        result = await whatsapp_ai_bot._run_tool(
            db=db, parent_id=parent_with_child.parent.id,
            name="get_child_teacher",
            tool_input={"child_id": str(stranger_id)},
        )
        assert result["error"] == "not_allowed"

    async def test_run_tool_unknown_name(
        self, db: AsyncSession, parent_with_child
    ):
        result = await whatsapp_ai_bot._run_tool(
            db=db, parent_id=parent_with_child.parent.id,
            name="delete_the_school",
            tool_input={},
        )
        assert "Unknown tool" in result["error"]

    async def test_get_my_children_via_executor(
        self, db: AsyncSession, parent_with_child
    ):
        result = await whatsapp_ai_bot._run_tool(
            db=db, parent_id=parent_with_child.parent.id,
            name="get_my_children", tool_input={},
        )
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["first_name"] == "Sipho"
        assert result[0]["class_name"] == "Grade 2B"


# ---------------------------------------------------------------------------
# AI loop — mocked Anthropic client
# ---------------------------------------------------------------------------

def _mk_text_response(text: str, stop_reason: str = "end_turn"):
    """Fake Anthropic response — one text block, end_turn."""
    block = SimpleNamespace(
        type="text", text=text,
        model_dump=lambda: {"type": "text", "text": text},
    )
    return SimpleNamespace(
        content=[block],
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=100, output_tokens=20),
    )


def _mk_tool_use_response(tool_name: str, tool_input: dict, tool_use_id: str = "tu_1"):
    """Fake Anthropic response — one tool_use block, stop_reason=tool_use."""
    block = SimpleNamespace(
        type="tool_use", name=tool_name, input=tool_input, id=tool_use_id,
        model_dump=lambda: {
            "type": "tool_use", "name": tool_name,
            "input": tool_input, "id": tool_use_id,
        },
    )
    return SimpleNamespace(
        content=[block],
        stop_reason="tool_use",
        usage=SimpleNamespace(input_tokens=200, output_tokens=50),
    )


class TestAILoop:
    async def test_single_turn_text_reply(
        self, db: AsyncSession, parent_with_child
    ):
        """No tool calls — Claude just replies. Simplest happy path."""
        cfg = AIConfig(
            api_key="sk-test", model="claude-haiku-4-5",
            daily_message_cap_per_user=200, max_conversation_turns=10,
        )
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            return_value=_mk_text_response("Hi Nomsa! How can I help?"),
        )
        with patch(
            "anthropic.AsyncAnthropic",
            return_value=mock_client,
        ), patch(
            "app.services.whatsapp_ai_bot.get_bot_session_store",
            return_value=SimpleNamespace(
                get_history=AsyncMock(return_value=[]),
                save_history=AsyncMock(),
            ),
        ):
            reply = await whatsapp_ai_bot.handle_ai_message(
                db=db, user=parent_with_child.parent,
                tenant_name=parent_with_child.tenant.name,
                text="hello", cfg=cfg,
            )
        assert isinstance(reply, TextReply)
        assert "Nomsa" in reply.body
        mock_client.messages.create.assert_awaited_once()

    async def test_tool_use_round_trip(
        self, db: AsyncSession, parent_with_child
    ):
        """Claude asks for get_my_children, we execute, Claude then
        replies with the final text. Verifies the loop advances."""
        cfg = AIConfig(
            api_key="sk-test", model="claude-haiku-4-5",
            daily_message_cap_per_user=200, max_conversation_turns=10,
        )
        mock_client = MagicMock()
        # Two-step: first tool_use, then text.
        mock_client.messages.create = AsyncMock(side_effect=[
            _mk_tool_use_response("get_my_children", {}),
            _mk_text_response("You have 1 child, Sipho in Grade 2B."),
        ])
        with patch(
            "anthropic.AsyncAnthropic",
            return_value=mock_client,
        ), patch(
            "app.services.whatsapp_ai_bot.get_bot_session_store",
            return_value=SimpleNamespace(
                get_history=AsyncMock(return_value=[]),
                save_history=AsyncMock(),
            ),
        ):
            reply = await whatsapp_ai_bot.handle_ai_message(
                db=db, user=parent_with_child.parent,
                tenant_name=parent_with_child.tenant.name,
                text="Who are my kids?", cfg=cfg,
            )
        assert isinstance(reply, TextReply)
        assert "Sipho" in reply.body
        assert mock_client.messages.create.await_count == 2

    async def test_loop_bails_on_iteration_cap(
        self, db: AsyncSession, parent_with_child
    ):
        """Bogus Claude that keeps demanding tools forever — the loop
        must stop at MAX_LOOP_ITERATIONS and return a canned message."""
        cfg = AIConfig(
            api_key="sk-test", model="claude-haiku-4-5",
            daily_message_cap_per_user=200, max_conversation_turns=10,
        )
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            return_value=_mk_tool_use_response("get_my_children", {}),
        )
        with patch(
            "anthropic.AsyncAnthropic",
            return_value=mock_client,
        ), patch(
            "app.services.whatsapp_ai_bot.get_bot_session_store",
            return_value=SimpleNamespace(
                get_history=AsyncMock(return_value=[]),
                save_history=AsyncMock(),
            ),
        ):
            reply = await whatsapp_ai_bot.handle_ai_message(
                db=db, user=parent_with_child.parent,
                tenant_name=parent_with_child.tenant.name,
                text="loop forever", cfg=cfg,
            )
        assert isinstance(reply, TextReply)
        assert "one thing at a time" in reply.body
        assert mock_client.messages.create.await_count == whatsapp_ai_bot.MAX_LOOP_ITERATIONS

    async def test_anthropic_error_falls_back_to_menu(
        self, db: AsyncSession, parent_with_child
    ):
        """Anthropic throws — user must still get a useful reply, not
        silence. Menu bot's main menu is the fallback."""
        cfg = AIConfig(
            api_key="sk-test", model="claude-haiku-4-5",
            daily_message_cap_per_user=200, max_conversation_turns=10,
        )
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            side_effect=RuntimeError("Anthropic down"),
        )
        with patch(
            "anthropic.AsyncAnthropic",
            return_value=mock_client,
        ), patch(
            "app.services.whatsapp_ai_bot.get_bot_session_store",
            return_value=SimpleNamespace(
                get_history=AsyncMock(return_value=[]),
                save_history=AsyncMock(),
            ),
        ):
            reply = await whatsapp_ai_bot.handle_ai_message(
                db=db, user=parent_with_child.parent,
                tenant_name=parent_with_child.tenant.name,
                text="what's my balance", cfg=cfg,
            )
        assert isinstance(reply, TextReply)
        # Menu fallback flattens the list into text with a section header.
        assert "Balance" in reply.body or "menu" in reply.body.lower()

    async def test_unconfigured_key_falls_back(
        self, db: AsyncSession, parent_with_child
    ):
        cfg = AIConfig(
            api_key="", model="claude-haiku-4-5",
            daily_message_cap_per_user=200, max_conversation_turns=10,
        )
        reply = await whatsapp_ai_bot.handle_ai_message(
            db=db, user=parent_with_child.parent,
            tenant_name=parent_with_child.tenant.name,
            text="hi", cfg=cfg,
        )
        assert isinstance(reply, TextReply)
        # Menu-bot flattened response — should mention at least one option.
        assert reply.body  # non-empty

    async def test_corrupt_history_400_clears_session_and_retries(
        self, db: AsyncSession, parent_with_child
    ):
        """A poisoned Redis session (orphan tool_result blocks) 400s
        Claude. The outer handler must clear the session, retry ONCE
        with a fresh conversation, and only fall through to menu bot
        if the retry also fails."""
        cfg = AIConfig(
            api_key="sk-test", model="claude-haiku-4-5",
            daily_message_cap_per_user=200, max_conversation_turns=10,
        )

        # First call: raise a 400 mimicking Anthropic's exact error
        # text. Second call: succeed with a text reply (proves the
        # retry ran).
        bad_400 = RuntimeError(
            "Error code: 400 - messages.0.content.0: unexpected "
            "tool_use_id found in tool_result blocks"
        )
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(side_effect=[
            bad_400,
            _mk_text_response("Retry worked, hi Nomsa."),
        ])

        mock_store = SimpleNamespace(
            get_history=AsyncMock(return_value=[
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "orphan"}]},
            ]),
            save_history=AsyncMock(),
            clear=AsyncMock(),
        )

        with patch(
            "anthropic.AsyncAnthropic", return_value=mock_client,
        ), patch(
            "app.services.whatsapp_ai_bot.get_bot_session_store",
            return_value=mock_store,
        ):
            reply = await whatsapp_ai_bot.handle_ai_message(
                db=db, user=parent_with_child.parent,
                tenant_name=parent_with_child.tenant.name,
                text="hi", cfg=cfg,
            )
        assert isinstance(reply, TextReply)
        assert "Retry worked" in reply.body
        # Session was cleared exactly once before the retry.
        mock_store.clear.assert_awaited_once()
        # Claude was called twice — the failing first attempt + the retry.
        assert mock_client.messages.create.await_count == 2


class TestCleanTextOnlyTurns:
    """The save-side sanitiser — must never emit a message list that
    could 400 Anthropic when re-loaded and truncated."""

    def test_strips_tool_use_blocks_from_assistant(self):
        messages = [
            {"role": "user", "content": "balance"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "get_child_balance",
                     "input": {"child_id": "abc"}, "id": "tu_1"},
                    {"type": "text", "text": "Sarah owes R500."},
                ],
            },
        ]
        out = whatsapp_ai_bot._clean_text_only_turns(messages)
        assert len(out) == 2
        assert out[0] == {"role": "user", "content": "balance"}
        # Assistant kept — only the text block survives.
        assert out[1]["role"] == "assistant"
        assert all(b["type"] == "text" for b in out[1]["content"])
        assert "Sarah owes R500" in out[1]["content"][0]["text"]

    def test_drops_user_tool_result_messages(self):
        # A user message that's ONLY tool_result blocks (the reply we
        # send back to Claude after executing tools) has no human
        # content — dropping it entirely is safer than keeping half a
        # tool exchange.
        messages = [
            {"role": "user", "content": "balance"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "get_child_balance",
                     "input": {"child_id": "abc"}, "id": "tu_1"},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu_1",
                     "content": "{\"balance\": 500}"},
                ],
            },
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Sarah owes R500."}],
            },
        ]
        out = whatsapp_ai_bot._clean_text_only_turns(messages)
        # Only the text turns survive: user "balance" + assistant answer.
        # The tool_use-only assistant + tool_result user get dropped so
        # truncation can never orphan them.
        roles = [m["role"] for m in out]
        assert roles == ["user", "assistant"]
        assert out[0]["content"] == "balance"
        assert "Sarah owes R500" in out[1]["content"][0]["text"]

    def test_truncation_after_cleaning_is_always_safe(self):
        """The whole point — after cleaning + rolling-window trim to
        the last N messages, we never leave an orphan tool_result at
        position 0 (which is the 400 Claude gave us)."""
        # A long conversation with tool calls in every turn.
        long_history = []
        for i in range(20):
            long_history.append({"role": "user", "content": f"q{i}"})
            long_history.append({
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "get_my_children",
                     "input": {}, "id": f"tu_{i}"},
                ],
            })
            long_history.append({
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": f"tu_{i}",
                     "content": "[]"},
                ],
            })
            long_history.append({
                "role": "assistant",
                "content": [{"type": "text", "text": f"answer {i}"}],
            })

        cleaned = whatsapp_ai_bot._clean_text_only_turns(long_history)
        # Every kept message is EITHER a user-string OR an assistant with
        # text blocks only. No tool_result orphans possible.
        for m in cleaned:
            content = m["content"]
            if isinstance(content, list):
                for b in content:
                    assert b.get("type") == "text", (
                        f"leaked non-text block after cleaning: {b}"
                    )
        # Truncate to last 5 — even the strictest truncation stays valid.
        window = cleaned[-5:]
        for m in window:
            content = m["content"]
            if isinstance(content, list):
                for b in content:
                    assert b.get("type") == "text"
