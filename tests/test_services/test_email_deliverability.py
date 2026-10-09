"""Tests for the email_service deliverability helpers.

Covers the three code-level pieces of the Gmail-spam fix:
  - _html_to_plain renders a usable plaintext fallback
  - _list_unsubscribe_headers returns both mailto and https forms
  - send() builds a multipart/alternative body with both parts and
    attaches List-Unsubscribe headers before invoking the provider

DNS-level pieces (SPF/DKIM/DMARC) are out of scope for unit tests —
those live in the DNS checklist alongside this file.
"""

from __future__ import annotations

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from unittest.mock import AsyncMock, patch

import pytest

from app.services.email_service import (
    EmailService,
    _html_to_plain,
    _list_unsubscribe_headers,
)


class TestHtmlToPlain:
    def test_preserves_paragraph_breaks(self):
        html = "<p>Hello,</p><p>Welcome to ClassUp.</p>"
        text = _html_to_plain(html)
        assert "Hello," in text
        assert "Welcome to ClassUp." in text
        # Two paragraphs should be on separate lines.
        assert text.index("Hello,") < text.index("Welcome")

    def test_renders_links_with_href(self):
        html = '<p>Click <a href="https://example.com/x">here</a>.</p>'
        text = _html_to_plain(html)
        assert "here" in text
        assert "https://example.com/x" in text

    def test_strips_script_and_style(self):
        html = "<style>.x{color:red}</style><p>Body</p><script>alert(1)</script>"
        text = _html_to_plain(html)
        assert "Body" in text
        assert "color:red" not in text
        assert "alert" not in text

    def test_handles_malformed_html_gracefully(self):
        # Should not raise even on obviously broken input.
        html = "<p>unclosed <strong>bold <em>double"
        text = _html_to_plain(html)
        assert "unclosed" in text
        assert "bold" in text


class TestListUnsubscribeHeaders:
    def test_returns_mailto_when_no_base_url(self):
        headers = _list_unsubscribe_headers(
            "parent@example.com", "notifications@classup.co.za", "",
        )
        assert "List-Unsubscribe" in headers
        assert "mailto:unsubscribe@classup.co.za" in headers["List-Unsubscribe"]
        # One-Click POST header is omitted when there's no http URL
        # to post to — Gmail then falls back to the mailto.
        assert "List-Unsubscribe-Post" not in headers

    def test_returns_https_plus_mailto_with_base_url(self):
        headers = _list_unsubscribe_headers(
            "parent@example.com",
            "notifications@classup.co.za",
            "https://app.classup.co.za/",
        )
        assert "List-Unsubscribe-Post" in headers
        assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
        # Must contain BOTH https and mailto, so Gmail/Yahoo prefer
        # the one-click and legacy clients still work.
        value = headers["List-Unsubscribe"]
        assert "https://app.classup.co.za/unsubscribe?email=parent%40example.com" in value
        assert "mailto:unsubscribe@classup.co.za" in value

    def test_url_encodes_plus_sign_in_email(self):
        # Addresses with a `+` tag have to be URL-encoded or Gmail
        # drops the tag and we unsubscribe the wrong parent.
        headers = _list_unsubscribe_headers(
            "parent+tag@example.com",
            "notifications@classup.co.za",
            "https://app.classup.co.za",
        )
        assert "parent%2Btag%40example.com" in headers["List-Unsubscribe"]


class TestSendBuildsPlaintextAndHeaders:
    @pytest.mark.asyncio
    async def test_send_builds_both_parts_and_passes_headers(self):
        service = EmailService()

        with patch(
            "app.services.email_service._load_email_config",
            new=AsyncMock(return_value={
                "provider": "smtp",
                "enabled": True,
                "from_email": "notifications@classup.co.za",
                "from_name": "ClassUp",
                "smtp_host": "smtp.example.com",
                "smtp_port": 587,
                "smtp_use_tls": True,
                "smtp_username": "user",
                "smtp_password": "pw",
            }),
        ), patch.object(
            service, "_send_via_smtp", new=AsyncMock(return_value="msgid-1"),
        ) as smtp_mock, patch.object(
            service, "_render_template", return_value="<p>Hello Russel</p>",
        ):
            await service.send(
                to="parent@example.com",
                subject="Test",
                template_name="whatever.html",
                context={"tenant_email": "school@example.com"},
            )

        assert smtp_mock.await_count == 1
        kwargs = smtp_mock.call_args.kwargs
        args = smtp_mock.call_args.args
        # Positional: config, from_address, from_email, recipients,
        #             subject, html_body, text_body, reply_to, cc, bcc, attachments, extra_headers
        text_body = args[6]
        reply_to = args[7]
        extra_headers = args[-1]

        assert "Hello Russel" in text_body
        # tenant_email in context should become the Reply-To.
        assert reply_to == "school@example.com"
        assert "List-Unsubscribe" in extra_headers

    @pytest.mark.asyncio
    async def test_send_falls_back_to_no_reply_to_when_absent(self):
        service = EmailService()

        with patch(
            "app.services.email_service._load_email_config",
            new=AsyncMock(return_value={
                "provider": "smtp",
                "enabled": True,
                "from_email": "notifications@classup.co.za",
                "from_name": "ClassUp",
                "smtp_host": "smtp.example.com",
                "smtp_port": 587,
                "smtp_use_tls": True,
            }),
        ), patch.object(
            service, "_send_via_smtp", new=AsyncMock(return_value="msgid-1"),
        ) as smtp_mock, patch.object(
            service, "_render_template", return_value="<p>Hi</p>",
        ):
            await service.send(
                to="parent@example.com",
                subject="Test",
                template_name="whatever.html",
                context={},  # no tenant_email
            )

        reply_to = smtp_mock.call_args.args[7]
        assert reply_to is None
