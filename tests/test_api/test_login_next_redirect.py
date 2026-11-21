"""Login ``next=`` must never redirect off-site (open-redirect guard)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import Response
from starlette.requests import Request

from app.web._parent_context import safe_next_path


@pytest.mark.parametrize("raw", [
    None, "", "https://evil.example", "//evil.example", "/\\evil.example",
    "/\t/evil.example", "/\n/evil.example", "javascript:alert(1)", "evil.example",
])
def test_unsafe_next_falls_back(raw):
    assert safe_next_path(raw) == "/dashboard"


@pytest.mark.parametrize("raw", ["/dashboard", "/reports/abc?child_id=1", "/billing/statement"])
def test_same_site_next_kept(raw):
    assert safe_next_path(raw) == raw


class _FakeAuth:
    async def login(self, db, req):
        return SimpleNamespace(access_token="tok"), SimpleNamespace()


async def _login(monkeypatch, next_value: str):
    from app.web import auth as web_auth
    monkeypatch.setattr(web_auth, "get_auth_service", lambda: _FakeAuth())
    request = Request({"type": "http", "method": "POST", "path": "/login",
                       "headers": [], "query_string": b""})
    return await web_auth.login_submit(
        request, Response(), email="parent@example.com", password="password123", remember_me=False,
        next=next_value, tenant_slug=None, db=None,
    )


async def test_login_ignores_offsite_next(monkeypatch):
    resp = await _login(monkeypatch, "https://evil.example/phish")
    assert resp.status_code == 302
    assert resp.headers["location"] == "/dashboard"


async def test_login_honours_same_site_next(monkeypatch):
    resp = await _login(monkeypatch, "/billing/statement")
    assert resp.status_code == 302
    assert resp.headers["location"] == "/billing/statement"
