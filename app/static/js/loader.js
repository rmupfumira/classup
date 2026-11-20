/*
 * Page loader — shows the full-page brand-mark overlay during same-
 * origin navigations and form submits, hides it on bfcache restore.
 *
 * Design choices:
 *  - 150ms show-delay so fast navigations (local routes already in
 *    the cache, cached assets) never flash the overlay — the human
 *    eye reads anything under ~200ms as "instant" and a flicker
 *    there feels worse than no feedback at all.
 *  - Since every page is a fresh SSR document, we don't need to
 *    "cancel" the pending show on navigation — the new page starts
 *    with the overlay hidden. We only need to hide it when the
 *    browser restores this page from bfcache (back/forward).
 *  - Skips modifier-clicks, target=_blank, download links, hash-only
 *    links, and anything with data-no-loader opt-out.
 */
(function () {
    'use strict';

    const SHOW_DELAY_MS = 150;

    const overlay = document.getElementById('page-loader');
    if (!overlay) return;

    let pendingShow = null;

    function show() {
        overlay.classList.add('visible');
        overlay.setAttribute('aria-hidden', 'false');
    }
    function hide() {
        overlay.classList.remove('visible');
        overlay.setAttribute('aria-hidden', 'true');
        if (pendingShow) {
            clearTimeout(pendingShow);
            pendingShow = null;
        }
    }
    function scheduleShow() {
        if (pendingShow) return;
        pendingShow = setTimeout(show, SHOW_DELAY_MS);
    }

    function shouldSkipLink(link, ev) {
        // Modifier-click opens a new tab/window → current page stays
        // put, no loader needed.
        if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return true;
        if (ev.button !== 0) return true;
        if (link.target && link.target !== '' && link.target !== '_self') return true;
        if (link.hasAttribute('download')) return true;
        if (link.hasAttribute('data-no-loader')) return true;
        // Explicit opt-out for in-page interactions like dropdown
        // triggers and dialog openers.
        if (link.getAttribute('role') === 'button') return true;

        const href = link.getAttribute('href');
        if (!href) return true;
        if (href.startsWith('#')) return true;
        if (href.startsWith('javascript:')) return true;
        if (href.startsWith('mailto:')) return true;
        if (href.startsWith('tel:')) return true;

        // Cross-origin — the browser hands off to another site, we
        // have no way to know when the new page paints, and the
        // overlay would linger on the way back.
        try {
            const url = new URL(href, window.location.href);
            if (url.origin !== window.location.origin) return true;
        } catch (e) {
            return true;
        }
        return false;
    }

    document.addEventListener('click', (ev) => {
        const link = ev.target.closest('a[href]');
        if (!link) return;
        if (shouldSkipLink(link, ev)) return;
        scheduleShow();
    });

    document.addEventListener('submit', (ev) => {
        const form = ev.target;
        if (!(form instanceof HTMLFormElement)) return;
        if (form.hasAttribute('data-no-loader')) return;
        // Forms that stay on the page (fetch-based, AJAX) will cancel
        // the default; we see the submit event first so we would flash
        // the overlay — mitigation: let the handler cancel default
        // synchronously, which means ev.defaultPrevented is already
        // true by the time this listener runs IF a handler earlier in
        // the capture/bubble chain called preventDefault. For bubble
        // handlers on the same element the ordering is undefined per
        // spec, so we also give JS-driven forms the opt-out attribute.
        if (ev.defaultPrevented) return;
        scheduleShow();
    });

    // Browser back/forward may restore this page from bfcache with
    // the overlay still visible from an earlier click.
    window.addEventListener('pageshow', (ev) => {
        if (ev.persisted) hide();
    });
    // On initial page paint the overlay should never be visible —
    // belt-and-braces in case a stylesheet race left it showing.
    window.addEventListener('DOMContentLoaded', hide);
})();
