"""Submit the 5 ClassUp WhatsApp templates to Meta for approval.

Uses the WhatsApp Business Management API (the same access_token +
business_account_id that /admin/whatsapp-settings already stores). Reads
the config from ``system_settings.whatsapp_config`` — no separate creds
to manage.

Idempotent-ish: if a template with the same name already exists on the
WABA, Meta returns a specific error (subcode ~2388023) which we log and
skip. Safe to re-run.

Run once per instance:

    WORKER_MODE=false python -m scripts.submit_whatsapp_templates

Templates land as "Pending review" in Meta Business Manager. Utility
templates usually approve in 5-30 minutes. Once green, ClassUp's
parent_notifier + bot outbound sends start working immediately — no
code change needed, the code already calls these exact names.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import httpx

# The 5 templates ClassUp expects. Body copy tuned to work well in a
# WhatsApp bubble: short first line, key info bolded via *asterisks*.
# Each {{N}} maps 1:1 to a param passed from app/services/whatsapp_service.py.
TEMPLATES: list[dict[str, Any]] = [
    {
        "name": "attendance_alert",
        "language": "en_US",
        "category": "UTILITY",
        "components": [
            {
                "type": "BODY",
                "text": (
                    "Hi! Attendance update for {{1}}: marked *{{2}}* today "
                    "at {{3}}."
                ),
                "example": {
                    "body_text": [["Sarah Moyo", "ABSENT", "Kingsway Primary"]]
                },
            }
        ],
    },
    {
        "name": "report_ready",
        "language": "en_US",
        "category": "UTILITY",
        "components": [
            {
                "type": "BODY",
                "text": (
                    "{{1}} is ready for {{2}}. View it here: {{3}}"
                ),
                "example": {
                    "body_text": [[
                        "Term 2 Progress Report",
                        "Sarah Moyo",
                        "https://classup.co.za/reports/abc123",
                    ]]
                },
            }
        ],
    },
    {
        "name": "announcement",
        "language": "en_US",
        "category": "UTILITY",
        "components": [
            {
                "type": "BODY",
                "text": "Update from {{1}}: {{2}}",
                "example": {
                    "body_text": [[
                        "Kingsway Primary",
                        "Reminder: parent-teacher meetings this Thursday 3-5pm.",
                    ]]
                },
            }
        ],
    },
    # Meta rejected the original ``parent_invite`` (Utility) as
    # miscategorised, and the name is locked out for 30 days after
    # deletion. Resubmitted as ``parent_signup`` under MARKETING —
    # Meta refuses to treat a signup CTA as Utility. Parents have
    # already opted in via ``users.whatsapp_opted_in`` so Marketing
    # is equally fine on our side. The code moves out of the body
    # into a dynamic URL button, so the body reads as pure copy and
    # avoids anything that looks like an authentication token.
    {
        "name": "parent_signup",
        "language": "en_US",
        "category": "MARKETING",
        "components": [
            {
                "type": "BODY",
                "text": (
                    "Welcome to ClassUp! *{{1}}* has invited you as a "
                    "parent. Tap the button below to complete your "
                    "signup and start receiving updates about your "
                    "child. See you soon!"
                ),
                "example": {
                    "body_text": [["Kingsway Primary"]]
                },
            },
            {
                "type": "BUTTONS",
                "buttons": [
                    {
                        "type": "URL",
                        "text": "Complete signup",
                        "url": "https://classup.co.za/register?code={{1}}",
                        "example": [
                            "https://classup.co.za/register?code=A3F7B2K9"
                        ],
                    }
                ],
            },
        ],
    },
    {
        "name": "welcome",
        "language": "en_US",
        "category": "UTILITY",
        "components": [
            {
                "type": "BODY",
                "text": (
                    "Welcome to {{1}} on ClassUp! Log in here to get "
                    "started: {{2}}"
                ),
                "example": {
                    "body_text": [[
                        "Kingsway Primary",
                        "https://classup.co.za/login",
                    ]]
                },
            }
        ],
    },
    # ── Payment templates (invoice_sent, payment_received, invoice_overdue) ──
    # Each carries a dynamic URL button that deep-links to
    # /billing/invoices/{{1}} — parent taps and lands on the invoice
    # view (PDF download, banking details, payment history).
    # No DOCUMENT header on invoice_sent — that requires a two-step
    # sample upload. Email still attaches the PDF; WhatsApp is the
    # "you've got an invoice, tap to view" nudge.
    {
        "name": "invoice_sent",
        "language": "en_US",
        "category": "UTILITY",
        "components": [
            {
                "type": "BODY",
                "text": (
                    "Hi {{1}}, invoice *{{2}}* for {{3}} — *{{4}}* due by "
                    "{{5}}. Tap below to view."
                ),
                "example": {
                    "body_text": [[
                        "Nomsa", "INV-2026-0001", "Sarah Moyo",
                        "R 1,500.00", "15 Oct 2026",
                    ]]
                },
            },
            {
                "type": "BUTTONS",
                "buttons": [
                    {
                        "type": "URL",
                        "text": "View invoice",
                        "url": "https://classup.co.za/billing/invoices/{{1}}",
                        "example": ["https://classup.co.za/billing/invoices/abc-123"],
                    }
                ],
            },
        ],
    },
    {
        "name": "payment_received",
        "language": "en_US",
        "category": "UTILITY",
        "components": [
            {
                "type": "BODY",
                "text": (
                    "Hi {{1}}, your payment of *{{2}}* for {{3}} ({{4}}) "
                    "has been received on {{5}}. Thank you!"
                ),
                "example": {
                    "body_text": [[
                        "Nomsa", "R 500.00", "Sarah Moyo",
                        "INV-2026-0001", "10 Sep 2026",
                    ]]
                },
            },
            {
                "type": "BUTTONS",
                "buttons": [
                    {
                        "type": "URL",
                        "text": "Receipt",
                        "url": "https://classup.co.za/billing/invoices/{{1}}",
                        "example": ["https://classup.co.za/billing/invoices/abc-123"],
                    }
                ],
            },
        ],
    },
    {
        "name": "invoice_overdue",
        "language": "en_US",
        "category": "UTILITY",
        "components": [
            {
                "type": "BODY",
                "text": (
                    "Payment reminder:\n\n"
                    "Account: {{1}}\n"
                    "Amount due: {{2}}\n"
                    "Due date: {{3}}\n\n"
                    "Pay now to avoid {{4}}.\n\n"
                    "Please ignore this if you have already paid."
                ),
                "example": {
                    "body_text": [[
                        "Sarah Moyo", "R 1,500.00", "15 Sep 2026",
                        "additional late fees",
                    ]]
                },
            },
            {
                "type": "BUTTONS",
                "buttons": [
                    {
                        "type": "URL",
                        "text": "Pay now",
                        "url": "https://classup.co.za/billing/invoices/{{1}}",
                        "example": ["https://classup.co.za/billing/invoices/abc-123"],
                    }
                ],
            },
        ],
    },
    # ── Event templates ──
    # URL button deep-links to /events/{{1}} — the detail page with
    # RSVP buttons.
    {
        "name": "event_invited",
        "language": "en_US",
        "category": "UTILITY",
        "components": [
            {
                "type": "BODY",
                "text": (
                    "You have an upcoming event: *{{1}}*. "
                    "Starts on {{2}} at {{3}}."
                ),
                "example": {
                    "body_text": [[
                        "Parent-Teacher Conference",
                        "Thu 15 Oct 2026, 15:00",
                        "School Hall",
                    ]]
                },
            },
            {
                "type": "BUTTONS",
                "buttons": [
                    {
                        "type": "URL",
                        "text": "View event",
                        "url": "https://classup.co.za/events/{{1}}",
                        "example": ["https://classup.co.za/events/abc-123"],
                    }
                ],
            },
        ],
    },
    {
        "name": "event_reminder",
        "language": "en_US",
        "category": "UTILITY",
        "components": [
            {
                "type": "BODY",
                "text": "Reminder: *{{1}}* is coming up on {{2}}.",
                "example": {
                    "body_text": [[
                        "Parent-Teacher Conference",
                        "Thu 15 Oct 2026, 15:00",
                    ]]
                },
            },
            {
                "type": "BUTTONS",
                "buttons": [
                    {
                        "type": "URL",
                        "text": "View event",
                        "url": "https://classup.co.za/events/{{1}}",
                        "example": ["https://classup.co.za/events/abc-123"],
                    }
                ],
            },
        ],
    },
    {
        "name": "event_rsvp_confirmed",
        "language": "en_US",
        "category": "UTILITY",
        "components": [
            {
                "type": "BODY",
                "text": (
                    "Thanks for RSVPing to *{{1}}* — you've responded "
                    "*{{2}}*. See you on {{3}}."
                ),
                "example": {
                    "body_text": [[
                        "Parent-Teacher Conference", "Yes",
                        "Thu 15 Oct 2026, 15:00",
                    ]]
                },
            },
            {
                "type": "BUTTONS",
                "buttons": [
                    {
                        "type": "URL",
                        "text": "View event",
                        "url": "https://classup.co.za/events/{{1}}",
                        "example": ["https://classup.co.za/events/abc-123"],
                    }
                ],
            },
        ],
    },
]


async def _load_config() -> tuple[str, str]:
    """Load business_account_id + access_token from system_settings.

    Falls back to WHATSAPP_BUSINESS_ACCOUNT_ID / WHATSAPP_ACCESS_TOKEN
    env vars if the DB row is missing — useful for local runs against
    a fresh DB.
    """
    from app.database import async_session_factory
    from app.services.whatsapp_service import get_config

    async with async_session_factory() as db:
        cfg = await get_config(db)

    if not cfg.business_account_id:
        raise RuntimeError(
            "business_account_id not set — go to /admin/whatsapp-settings "
            "and fill in your Meta WABA ID first."
        )
    if not cfg.access_token:
        raise RuntimeError(
            "access_token not set — go to /admin/whatsapp-settings "
            "and paste your Meta System User access token first."
        )
    return cfg.business_account_id, cfg.access_token


async def _submit_one(
    client: httpx.AsyncClient, waba_id: str, token: str, template: dict
) -> tuple[str, bool, str]:
    """POST one template to /message_templates. Returns (name, ok, message)."""
    url = f"https://graph.facebook.com/v21.0/{waba_id}/message_templates"
    try:
        resp = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=template,
            timeout=15.0,
        )
    except httpx.HTTPError as e:
        return template["name"], False, f"network error: {e}"

    if resp.status_code == 200:
        data = resp.json()
        return template["name"], True, (
            f"submitted (id={data.get('id')}, status={data.get('status', 'PENDING')})"
        )

    # Meta returns 400 + a specific error for "template with same name
    # already exists" — treat as OK-idempotent, not a failure.
    try:
        err = resp.json().get("error", {})
    except Exception:
        return template["name"], False, f"HTTP {resp.status_code}: {resp.text[:200]}"

    msg = err.get("message", "")
    subcode = err.get("error_subcode")
    if "already exists" in msg.lower() or subcode == 2388023:
        return template["name"], True, "already exists on this WABA — skipping"

    return template["name"], False, (
        f"HTTP {resp.status_code}: {msg} (code={err.get('code')})"
    )


async def main() -> int:
    print("Loading WhatsApp config from system_settings…")
    try:
        waba_id, token = await _load_config()
    except Exception as e:
        print(f"✗ Config error: {e}", file=sys.stderr)
        return 2

    print(f"WABA: {waba_id}")
    print(f"Submitting {len(TEMPLATES)} templates to Meta…\n")

    ok_count = 0
    fail_count = 0
    async with httpx.AsyncClient() as client:
        for tpl in TEMPLATES:
            name, ok, msg = await _submit_one(client, waba_id, token, tpl)
            marker = "✓" if ok else "✗"
            print(f"  {marker} {name:20s}  {msg}")
            if ok:
                ok_count += 1
            else:
                fail_count += 1

    print()
    print(f"Done: {ok_count} submitted/exist, {fail_count} failed.")
    print(
        "Templates will appear as 'Pending review' in Meta Business Manager.\n"
        "Utility templates usually approve in 5-30 minutes."
    )
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
