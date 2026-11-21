"""Browser (Playwright) fixtures for the responsive / mobile-shell suite.

These tests drive a *running* ClassUp server in a real browser, so they
are opt-in and skip cleanly when any prerequisite is missing:

* ``playwright`` not installed            -> whole module skipped
* Chromium not installed (``playwright install chromium``) -> skipped
* server at ``CLASSUP_E2E_BASE_URL`` unreachable -> skipped
* a role's login fails                     -> that role's tests skipped

Configuration (environment variables, all optional):

    CLASSUP_E2E_BASE_URL          default http://localhost:8010
    CLASSUP_E2E_<ROLE>_EMAIL      ROLE in ADMIN, TEACHER, PARENT, SUPER
    CLASSUP_E2E_<ROLE>_PASSWORD   falls back to CLASSUP_E2E_PASSWORD
    CLASSUP_E2E_PASSWORD          default password123 (local demo data)

Run:  pytest -m e2e tests/test_responsive
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request

import pytest

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

BASE_URL = os.environ.get("CLASSUP_E2E_BASE_URL", "http://localhost:8010").rstrip("/")
DEFAULT_PASSWORD = os.environ.get("CLASSUP_E2E_PASSWORD", "password123")

# Local demo accounts (Shumba demo tenant + seed super admin). Override
# with env vars when pointing at another environment.
_DEFAULT_EMAILS = {
    "admin": "head@shumba.co.zw",
    "teacher": "teacher1@shumba.co.zw",
    "parent": "parent003@shumba.co.zw",  # two linked children
    "super": "superadmin@classup.example.com",
}

ROLES = tuple(_DEFAULT_EMAILS)

# Phone -> desktop. Every viewport the acceptance brief calls out.
VIEWPORTS = [
    (320, 640),
    (360, 740),
    (375, 812),
    (390, 844),
    (414, 896),
    (768, 1024),
    (1024, 768),
    (1280, 800),   # header pill nav appears at xl — tightest desktop width
    (1440, 900),
]
MOBILE_BREAKPOINT = 768  # Tailwind md


def creds(role: str) -> tuple[str, str]:
    key = role.upper()
    email = os.environ.get(f"CLASSUP_E2E_{key}_EMAIL", _DEFAULT_EMAILS[role])
    password = os.environ.get(f"CLASSUP_E2E_{key}_PASSWORD", DEFAULT_PASSWORD)
    return email, password


@pytest.fixture(scope="session")
def base_url() -> str:
    try:
        urllib.request.urlopen(f"{BASE_URL}/health", timeout=3)  # noqa: S310 - local URL
    except urllib.error.HTTPError:
        pass  # server answered; any status means it is up
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"ClassUp server not reachable at {BASE_URL}: {exc}")
    return BASE_URL


@pytest.fixture(scope="session")
def browser(base_url):
    try:
        pw = sync_api.sync_playwright().start()
    except Exception as exc:  # noqa: BLE001 - e.g. running inside an event loop
        pytest.skip(f"Playwright could not start: {exc}")
    try:
        b = pw.chromium.launch()
    except Exception as exc:  # noqa: BLE001 - browsers not installed
        pw.stop()
        pytest.skip(f"Chromium not available (run `playwright install chromium`): {exc}")
    yield b
    b.close()
    pw.stop()


@pytest.fixture(scope="session")
def storage_states(browser, base_url):
    """Log each role in once per session; tests reuse the cookies."""
    cache: dict[str, dict | None] = {}

    def get(role: str) -> dict:
        if role not in cache:
            email, password = creds(role)
            ctx = browser.new_context(base_url=base_url)
            try:
                resp = ctx.request.post(
                    "/api/v1/auth/login", data={"email": email, "password": password},
                )
                cache[role] = ctx.storage_state() if resp.ok else None
            finally:
                ctx.close()
        state = cache[role]
        if state is None:
            pytest.skip(f"login failed for {role} ({creds(role)[0]})")
        return state

    return get


@pytest.fixture
def open_page(browser, base_url, storage_states):
    """``open_page(role, (w, h), path)`` -> a logged-in Playwright page."""
    contexts = []

    def _open(role: str, viewport: tuple[int, int], path: str):
        w, h = viewport
        ctx = browser.new_context(
            base_url=base_url,
            viewport={"width": w, "height": h},
            storage_state=storage_states(role),
            is_mobile=w < MOBILE_BREAKPOINT,
            has_touch=w < MOBILE_BREAKPOINT,
            # The PWA service worker reloads the page once when it first
            # takes control; block it so the DOM under test stays put.
            service_workers="block",
        )
        contexts.append(ctx)
        page = ctx.new_page()
        page.goto(path, wait_until="domcontentloaded")
        page.wait_for_load_state("load")
        if "/login" in page.url:
            pytest.skip(f"{role} session not accepted for {path}")
        return page

    yield _open
    for ctx in contexts:
        ctx.close()
