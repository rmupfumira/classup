"""Responsive checks for the shared application shell.

Covers the contract portal pages build on: no sideways scroll at any
viewport, bottom tab bar + drawer below md (with the same destinations as
the desktop sidebar), 44px touch targets, the mobile search sheet, and the
parent child switcher. See tests/test_responsive/conftest.py for setup.
"""

from __future__ import annotations

import pytest

from .conftest import MOBILE_BREAKPOINT, VIEWPORTS

pytestmark = pytest.mark.e2e

# A few representative pages per role (shell + one dense page each).
PAGES = {
    "admin": ["/dashboard", "/students", "/attendance", "/billing"],
    "teacher": ["/dashboard", "/attendance", "/reports"],
    "parent": ["/dashboard", "/reports", "/messages"],
    "super": ["/admin", "/admin/tenants"],
}
PHONE = (375, 812)
SMALL_PHONE = (320, 640)

OVERFLOW_JS = """() => {
    const de = document.documentElement;
    const offenders = [...document.querySelectorAll('body *')].filter(el => {
        const r = el.getBoundingClientRect();
        if (!r.width || r.right <= innerWidth + 1) return false;
        // Content clipped by a scroll container (scroll tables) is fine.
        for (let p = el.parentElement; p; p = p.parentElement) {
            const ox = getComputedStyle(p).overflowX;
            if (ox === 'auto' || ox === 'scroll' || ox === 'hidden' || ox === 'clip') return false;
        }
        return true;
    }).slice(0, 5).map(el => el.tagName.toLowerCase() + '.' + String(el.className).slice(0, 60));
    return {scrollWidth: de.scrollWidth, innerWidth, offenders};
}"""


def _vp_id(vp):
    return f"{vp[0]}x{vp[1]}"


@pytest.mark.parametrize("viewport", VIEWPORTS, ids=_vp_id)
@pytest.mark.parametrize("role", list(PAGES))
def test_no_horizontal_overflow(open_page, role, viewport):
    first, *rest = PAGES[role]
    page = open_page(role, viewport, first)
    for path in [first, *rest]:
        if path != first:
            page.goto(path, wait_until="load")
        result = page.evaluate(OVERFLOW_JS)
        assert result["scrollWidth"] <= result["innerWidth"] + 1, (
            f"{role} {path} @ {_vp_id(viewport)} scrolls sideways: {result}"
        )


@pytest.mark.parametrize("viewport", VIEWPORTS, ids=_vp_id)
def test_navigation_chrome_per_breakpoint(open_page, viewport):
    page = open_page("admin", viewport, "/dashboard")
    tabbar = page.locator("nav.cu-tabbar")
    sidebar = page.locator("#cu-sidebar")
    if viewport[0] < MOBILE_BREAKPOINT:
        assert tabbar.is_visible()
        assert not sidebar.is_visible()
    else:
        assert not tabbar.is_visible()
        assert sidebar.is_visible()


@pytest.mark.parametrize("role", list(PAGES))
def test_drawer_matches_sidebar_and_closes(open_page, role):
    page = open_page(role, PHONE, PAGES[role][0])
    page.click("#mobile-menu-toggle")
    drawer = page.locator("#mobile-menu")
    page.wait_for_function("document.getElementById('mobile-menu').classList.contains('is-open')")

    sidebar_links = page.eval_on_selector_all(
        '#cu-sidebar nav[aria-label="Primary"] a[href]', "els => els.map(e => e.getAttribute('href'))",
    )
    drawer_links = page.eval_on_selector_all(
        "#mobile-nav-clone a[href]", "els => els.map(e => e.getAttribute('href'))",
    )
    assert sidebar_links, "desktop sidebar rendered no links"
    assert drawer_links == sidebar_links, "drawer must expose every sidebar destination"
    assert page.locator("#mobile-menu-toggle").get_attribute("aria-expanded") == "true"
    assert page.evaluate("document.documentElement.classList.contains('cu-scroll-lock')")

    # Collapsible group inside the drawer.
    header = drawer.locator(".nav-section-header[aria-expanded]").first
    if header.count():
        header.click()
        assert header.get_attribute("aria-expanded") == "false"
        header.click()

    page.keyboard.press("Escape")
    page.wait_for_function("!document.getElementById('mobile-menu').classList.contains('is-open')")
    assert page.locator("#mobile-menu-toggle").get_attribute("aria-expanded") == "false"
    assert not page.evaluate("document.documentElement.classList.contains('cu-scroll-lock')")

    # Backdrop closes it too.
    page.click("#mobile-menu-toggle")
    page.wait_for_function("document.getElementById('mobile-menu').classList.contains('is-open')")
    page.mouse.click(PHONE[0] - 10, PHONE[1] // 2)
    page.wait_for_function("!document.getElementById('mobile-menu').classList.contains('is-open')")


@pytest.mark.parametrize("viewport", [SMALL_PHONE, PHONE], ids=_vp_id)
@pytest.mark.parametrize("role", list(PAGES))
def test_touch_targets(open_page, role, viewport):
    page = open_page(role, viewport, PAGES[role][0])
    sizes = page.eval_on_selector_all(
        ".cu-tabbar a, .cu-tabbar button",
        "els => els.map(e => { const r = e.getBoundingClientRect(); return [e.textContent.trim(), r.width, r.height, r.right]; })",
    )
    assert len(sizes) == 5, sizes
    for label, w, h, right in sizes:
        assert w >= 44 and h >= 44, f"tab {label!r} is {w}x{h}"
        assert right <= viewport[0], f"tab {label!r} is off-screen"
    header_buttons = page.eval_on_selector_all(
        "header nav button:visible, header nav a:visible",
        "els => els.filter(e => e.offsetParent).map(e => [e.getAttribute('aria-label') || e.textContent.trim(), e.getBoundingClientRect().height])",
    )
    for label, h in header_buttons:
        assert h >= 40, f"header control {label!r} is only {h}px tall"


def test_header_controls_have_names(open_page):
    page = open_page("admin", PHONE, "/dashboard")
    unnamed = page.evaluate("""() => [...document.querySelectorAll('header button, .cu-tabbar a, .cu-tabbar button')]
        .filter(b => b.offsetParent && !(b.getAttribute('aria-label') || b.textContent.trim()))
        .map(b => b.outerHTML.slice(0, 80))""")
    assert unnamed == []


@pytest.mark.parametrize("viewport", [SMALL_PHONE, PHONE], ids=_vp_id)
def test_mobile_search_sheet(open_page, viewport):
    page = open_page("admin", viewport, "/dashboard")
    page.click("#mobile-search-toggle")
    search = page.locator("#global-search-input")
    assert search.is_visible()
    box = search.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= viewport[0]
    assert box["width"] >= viewport[0] * 0.6
    assert box["height"] >= 44
    assert page.evaluate("document.activeElement.id") == "global-search-input"
    page.keyboard.press("Escape")
    page.keyboard.press("Escape")
    assert not page.evaluate("document.documentElement.classList.contains('cu-search-open')")


def test_parent_child_switcher(open_page):
    page = open_page("parent", PHONE, "/dashboard")
    kids = page.evaluate("ClassUp.fetch('/api/v1/students/my-children').then(r => (r && r.data) || [])")
    page.click("#mobile-menu-toggle")
    if not kids:
        pytest.skip("demo parent has no linked children")
    page.wait_for_selector("#mobile-children:not(.hidden)")
    hrefs = page.eval_on_selector_all(
        "#mobile-children-list a", "els => els.map(e => e.getAttribute('href'))",
    )
    assert len(hrefs) == len(kids)
    # Child switching goes through the server-validated select route,
    # which stores the (re-checked) choice and returns to the current page.
    assert all(h.startswith("/dashboard/select-child?child_id=") for h in hrefs)


def test_teacher_bottom_bar_has_exams_or_students(open_page):
    page = open_page("teacher", PHONE, "/dashboard")
    hrefs = page.eval_on_selector_all(".cu-tabbar a", "els => els.map(e => e.getAttribute('href'))")
    assert "/attendance" in hrefs
    assert "/exams" in hrefs or "/students" in hrefs
