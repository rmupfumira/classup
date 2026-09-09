"""AI service configuration — Anthropic API key + model — stored in
system_settings.ai_config.

Follows the same pattern as email_service, whatsapp_service, and payment
gateway config:

  - DB values in ``system_settings`` win, env vars are the fallback.
  - Secrets round-trip via a MASKED placeholder so the admin UI can PUT
    the current config back without accidentally overwriting the key
    with the mask.
  - Fetched fresh on every send (there's no caching layer beneath here);
    if the admin rotates the key it takes effect on the next inbound
    WhatsApp message.

Scope: the AI settings live at the *platform* level, not per-tenant.
One ClassUp deployment = one Anthropic org; usage is metered by tenant
via message counts (Phase 2C+ observability) but the credential itself
is shared.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import SystemSettings

logger = logging.getLogger(__name__)
settings = get_settings()

AI_SETTINGS_KEY = "ai_config"
MASKED = "********"

# Default to Haiku 4.5 — cheapest current Claude, easily handles our
# tool-use pattern, sub-second responses feel snappy on WhatsApp.
DEFAULT_MODEL = "claude-haiku-4-5"


@dataclass(frozen=True)
class AIConfig:
    """Resolved AI credentials + settings."""

    api_key: str
    model: str
    daily_message_cap_per_user: int  # sanity limit; keeps a broken loop cheap
    max_conversation_turns: int      # last N turns kept in Redis (rolling)

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.model)

    def with_masked_secrets(self) -> dict[str, Any]:
        """Serialise for GET responses — hide the API key."""
        return {
            "api_key": MASKED if self.api_key else "",
            "model": self.model,
            "daily_message_cap_per_user": self.daily_message_cap_per_user,
            "max_conversation_turns": self.max_conversation_turns,
            "configured": self.configured,
        }


async def get_config(db: AsyncSession) -> AIConfig:
    """Return the effective AI config for this instance.

    DB row wins; env var (``ANTHROPIC_API_KEY``) is the fallback so local
    dev can just export the var and skip the admin UI.
    """
    result = await db.execute(
        select(SystemSettings).where(SystemSettings.key == AI_SETTINGS_KEY)
    )
    row = result.scalar_one_or_none()
    v = (row.value if row else None) or {}

    # ANTHROPIC_API_KEY isn't a top-level setting in config.Settings —
    # read directly from env so we don't need a schema change.
    import os as _os
    env_key = _os.environ.get("ANTHROPIC_API_KEY", "")

    return AIConfig(
        api_key=str(v.get("api_key") or env_key or "").strip(),
        model=str(v.get("model") or DEFAULT_MODEL).strip(),
        daily_message_cap_per_user=int(v.get("daily_message_cap_per_user") or 200),
        max_conversation_turns=int(v.get("max_conversation_turns") or 10),
    )


async def save_config(db: AsyncSession, updates: dict[str, Any]) -> AIConfig:
    """Upsert the AI config. MASKED preserves the existing API key on a
    round-trip PUT so admins can edit the model without re-entering the
    key."""
    existing = await get_config(db)

    allowed = {"api_key", "model", "daily_message_cap_per_user", "max_conversation_turns"}
    clean: dict[str, Any] = {}
    for k, v in (updates or {}).items():
        if k not in allowed:
            continue
        if k in {"daily_message_cap_per_user", "max_conversation_turns"}:
            try:
                clean[k] = int(v)
            except (TypeError, ValueError):
                # Ignore malformed numeric input rather than crashing the
                # save — the UI will re-render with the old value.
                continue
        else:
            clean[k] = str(v or "").strip()

    if clean.get("api_key") == MASKED:
        clean["api_key"] = existing.api_key

    row_result = await db.execute(
        select(SystemSettings).where(SystemSettings.key == AI_SETTINGS_KEY)
    )
    row = row_result.scalar_one_or_none()
    merged = {**(row.value if row else {}), **clean}
    if row:
        row.value = merged
    else:
        db.add(SystemSettings(key=AI_SETTINGS_KEY, value=merged))
    await db.flush()

    logger.info(
        "AI config saved (key set: %s, model: %s)",
        bool(merged.get("api_key")), merged.get("model"),
    )
    return await get_config(db)


async def test_connection(cfg: AIConfig) -> tuple[bool, str]:
    """Verify the Anthropic API key + model with a minimal call.

    Uses a tiny prompt (~10 input tokens, ~5 output) — under $0.0001 —
    just enough to prove the key is valid and the model exists.
    """
    if not cfg.api_key:
        return False, "API key is not set."
    try:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=cfg.api_key)
        resp = await client.messages.create(
            model=cfg.model,
            max_tokens=8,
            messages=[{"role": "user", "content": "Say OK."}],
        )
        text = ""
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text = block.text
                break
        return True, (
            f"OK — {cfg.model} responded ({resp.usage.input_tokens} in / "
            f"{resp.usage.output_tokens} out tokens): {text.strip()[:60]}"
        )
    except Exception as e:
        # Anthropic SDK raises typed errors — surface the class + message
        # so an admin can tell "invalid key" from "model not found" etc.
        return False, f"{type(e).__name__}: {str(e)[:200]}"
