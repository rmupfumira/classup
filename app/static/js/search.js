/*
 * Global search dropdown — navbar autocomplete.
 *
 * Modern touches, by section below:
 *  - 200ms debounced live dropdown as you type (min 2 chars)
 *  - Cmd/Ctrl+K from anywhere on the page focuses the field. "/" also
 *    focuses when the user isn't already typing in a form field —
 *    matches the single-key convention GitHub, Bluesky, Vercel use.
 *  - Match highlighting — the typed substring is wrapped in a soft
 *    gold background inside each result row so the hit is obvious.
 *  - Recents — when the field is focused and empty, show the last 5
 *    distinct queries from localStorage; clicking one re-runs it.
 *    Saved when the user actually navigates to a result or submits.
 *  - Keyboard nav: ↑/↓/Enter navigate; Esc closes + blurs; Enter on
 *    an empty-highlight submits the full query to /search?q=…
 *  - Stale-response dropper so a slow keystroke's result never
 *    overwrites a fresher one.
 */
(function () {
    'use strict';

    const SEARCH_INPUT_ID = 'global-search-input';
    const SEARCH_RESULTS_ID = 'global-search-results';
    const DEBOUNCE_MS = 200;
    const MIN_CHARS = 2;
    const RECENTS_KEY = 'cu.search.recents';
    const RECENTS_LIMIT = 5;

    const input = document.getElementById(SEARCH_INPUT_ID);
    const panel = document.getElementById(SEARCH_RESULTS_ID);
    if (!input || !panel) return;

    // Swap the Cmd/Ctrl hint for the platform's modifier symbol.
    // Done in JS so the server doesn't need to sniff user agents.
    const kbdHint = document.getElementById('global-search-kbd-hint');
    if (kbdHint) {
        const isMac = /Mac|iPhone|iPad|iPod/i.test(navigator.platform || '');
        kbdHint.textContent = isMac ? '⌘ K' : 'Ctrl K';
    }

    let debounceTimer = null;
    let currentItems = [];  // flat, in display order, for keyboard nav
    let activeIndex = -1;
    let latestRequestId = 0;

    // Icon map — keep in sync with templates/search/_icon.html
    const ICONS = {
        student: '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"/></svg>',
        class: '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M4.26 10.147a60.438 60.438 0 00-.491 6.347A48.62 48.62 0 0112 20.904a48.62 48.62 0 018.232-4.41 60.46 60.46 0 00-.491-6.347m-15.482 0a50.636 50.636 0 00-2.658-.813A59.906 59.906 0 0112 3.493a59.903 59.903 0 0110.399 5.84c-.896.248-1.783.52-2.658.814m-15.482 0A50.717 50.717 0 0112 13.489a50.702 50.702 0 017.74-3.342"/></svg>',
        teacher: '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M15 19.128a9.38 9.38 0 002.625.372 9.337 9.337 0 004.121-.952 4.125 4.125 0 00-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 018.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0111.964-3.07M12 6.375a3.375 3.375 0 11-6.75 0 3.375 3.375 0 016.75 0zm8.25 2.25a2.625 2.625 0 11-5.25 0 2.625 2.625 0 015.25 0z"/></svg>',
        parent: '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"/></svg>',
        invoice: '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>',
        tenant: '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M3 21h18M3 7v14M21 7v14M3 7l9-4 9 4M9 21v-4h6v4M9 11h.01M12 11h.01M15 11h.01M9 15h.01M12 15h.01M15 15h.01"/></svg>',
        admin: '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg>',
    };
    function iconFor(key) { return ICONS[key] || ICONS.student; }

    function openPanel() { panel.classList.remove('hidden'); }
    function closePanel() {
        panel.classList.add('hidden');
        activeIndex = -1;
    }

    function esc(s) {
        // Minimal HTML escape — the dropdown builds markup from server
        // data, which is already filtered by role but we never put
        // raw strings into innerHTML without sanitising.
        const d = document.createElement('div');
        d.textContent = (s == null ? '' : String(s));
        return d.innerHTML;
    }

    // Wrap case-insensitive substring matches with <mark>, HTML-safe.
    // esc() the text first, then do substring replace on the escaped
    // output — the typed query may contain `<` or `&` and must be
    // escaped before being used in the regex too.
    function highlight(text, query) {
        const safe = esc(text);
        const q = (query || '').trim();
        if (!q) return safe;
        const safeQ = esc(q);
        // Escape regex metacharacters in the query after HTML-escaping.
        const re = new RegExp(
            safeQ.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'),
            'gi',
        );
        return safe.replace(re, (m) => `<mark>${m}</mark>`);
    }

    // ---- recents (localStorage) ----
    // Wrapped in try/catch so a quota-blocked or private-mode browser
    // doesn't break the search.
    function loadRecents() {
        try {
            const raw = localStorage.getItem(RECENTS_KEY);
            if (!raw) return [];
            const arr = JSON.parse(raw);
            return Array.isArray(arr) ? arr.filter(q => typeof q === 'string').slice(0, RECENTS_LIMIT) : [];
        } catch (e) { return []; }
    }
    function saveRecent(q) {
        q = (q || '').trim();
        if (q.length < MIN_CHARS) return;
        try {
            const prev = loadRecents().filter(r => r.toLowerCase() !== q.toLowerCase());
            const next = [q, ...prev].slice(0, RECENTS_LIMIT);
            localStorage.setItem(RECENTS_KEY, JSON.stringify(next));
        } catch (e) { /* ignore */ }
    }

    function renderRecents() {
        const recents = loadRecents();
        currentItems = [];
        if (!recents.length) {
            panel.innerHTML = `
                <div class="px-4 py-6 text-center text-sm text-neutral-500">
                    Type to search
                    <p class="text-xs text-neutral-400 mt-1">Students, classes, teachers, invoices…</p>
                </div>`;
            return;
        }
        const parts = [`
            <div class="px-3 pt-2 pb-1 text-xs font-semibold text-neutral-400 uppercase tracking-wider">
                Recent
            </div>`];
        recents.forEach((r, i) => {
            parts.push(`
                <button type="button"
                        data-recent-index="${i}"
                        class="w-full flex items-center gap-3 px-3 py-2 hover:bg-neutral-50 transition-colors text-left">
                    <div class="flex-shrink-0 w-8 h-8 rounded-md bg-neutral-100 text-neutral-400 flex items-center justify-center">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
                                  d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/>
                        </svg>
                    </div>
                    <span class="flex-1 min-w-0 text-sm text-neutral-700 truncate">${esc(r)}</span>
                </button>`);
        });
        panel.innerHTML = parts.join('');
    }

    function render(groups, query) {
        currentItems = [];
        if (!groups || !groups.length) {
            panel.innerHTML = `
                <div class="px-4 py-6 text-center text-sm text-neutral-500">
                    No matches for <span class="font-medium text-neutral-700">"${esc(query)}"</span>
                </div>`;
            return;
        }
        const parts = [];
        let flatIndex = 0;
        for (const group of groups) {
            parts.push(`
                <div class="px-3 pt-2 pb-1 text-xs font-semibold text-neutral-400 uppercase tracking-wider">
                    ${esc(group.label)}
                </div>`);
            for (const item of group.items) {
                parts.push(`
                    <a href="${esc(item.url)}"
                       data-search-index="${flatIndex}"
                       class="flex items-center gap-3 px-3 py-2 hover:bg-neutral-50 transition-colors">
                        <div class="flex-shrink-0 w-8 h-8 rounded-md bg-primary-50 text-primary-600 flex items-center justify-center">
                            ${iconFor(item.icon)}
                        </div>
                        <div class="flex-1 min-w-0">
                            <p class="text-sm font-medium text-neutral-900 truncate">${highlight(item.title, query)}</p>
                            ${item.subtitle ? `<p class="text-xs text-neutral-500 truncate">${highlight(item.subtitle, query)}</p>` : ''}
                        </div>
                    </a>`);
                currentItems.push(item);
                flatIndex += 1;
            }
        }
        // "See all N results" footer link — takes the user to /search
        // where more per-category items are listed.
        const q = encodeURIComponent(query);
        parts.push(`
            <a href="/search?q=${q}"
               data-search-see-all
               class="block px-3 py-2 text-center text-xs font-medium text-primary-600 border-t border-neutral-100 hover:bg-neutral-50">
                See all results →
            </a>`);
        panel.innerHTML = parts.join('');
        activeIndex = -1;
    }

    function updateActiveHighlight() {
        const links = panel.querySelectorAll('a[data-search-index]');
        links.forEach((el, i) => {
            if (i === activeIndex) {
                el.classList.add('bg-primary-50');
                el.scrollIntoView({block: 'nearest'});
            } else {
                el.classList.remove('bg-primary-50');
            }
        });
    }

    async function runQuery(q) {
        const requestId = ++latestRequestId;
        try {
            const res = await ClassUp.fetch(
                `/api/v1/search?q=${encodeURIComponent(q)}&limit=5`,
                {method: 'GET'},
            );
            // Drop stale responses — a faster keystroke's result may
            // have come back first.
            if (requestId !== latestRequestId) return;
            if (!res || !res.data) return;
            render(res.data.groups, q);
            openPanel();
        } catch (err) {
            // Network error — fail silently; the field keeps working
            // with the next keystroke.
        }
    }

    input.addEventListener('input', () => {
        const q = input.value.trim();
        clearTimeout(debounceTimer);
        if (q.length < MIN_CHARS) {
            // Still open the panel, but with recents instead of a dead state.
            renderRecents();
            openPanel();
            return;
        }
        debounceTimer = setTimeout(() => runQuery(q), DEBOUNCE_MS);
    });

    input.addEventListener('keydown', (ev) => {
        if (ev.key === 'ArrowDown') {
            ev.preventDefault();
            if (!currentItems.length) return;
            activeIndex = Math.min(activeIndex + 1, currentItems.length - 1);
            updateActiveHighlight();
        } else if (ev.key === 'ArrowUp') {
            ev.preventDefault();
            if (!currentItems.length) return;
            activeIndex = Math.max(activeIndex - 1, -1);
            updateActiveHighlight();
        } else if (ev.key === 'Enter') {
            ev.preventDefault();
            const q = input.value.trim();
            if (activeIndex >= 0 && currentItems[activeIndex]) {
                saveRecent(q);
                window.location.href = currentItems[activeIndex].url;
            } else if (q.length >= MIN_CHARS) {
                saveRecent(q);
                window.location.href = '/search?q=' + encodeURIComponent(q);
            }
        } else if (ev.key === 'Escape') {
            closePanel();
            input.blur();
        }
    });

    // Delegated click handler — covers both result rows (save recent,
    // then let the browser navigate) and recent-chip buttons (re-run).
    panel.addEventListener('click', (ev) => {
        const row = ev.target.closest('a[data-search-index], a[data-search-see-all]');
        if (row) {
            saveRecent(input.value.trim());
            return;  // let the <a> navigate
        }
        const chip = ev.target.closest('button[data-recent-index]');
        if (chip) {
            const i = Number(chip.getAttribute('data-recent-index'));
            const q = loadRecents()[i];
            if (q) {
                input.value = q;
                runQuery(q);
            }
        }
    });

    // Close on outside click.
    document.addEventListener('click', (ev) => {
        if (!panel.contains(ev.target) && ev.target !== input) {
            closePanel();
        }
    });

    // On focus, show recents (if empty) or re-run the current query.
    input.addEventListener('focus', () => {
        const q = input.value.trim();
        if (q.length >= MIN_CHARS) {
            runQuery(q);
        } else {
            renderRecents();
            openPanel();
        }
    });

    // Global keyboard shortcut: Cmd/Ctrl+K anywhere focuses the field.
    // Also "/" when the user isn't already typing — matches the single-
    // key convention of GitHub, Bluesky, and most modern web apps.
    document.addEventListener('keydown', (ev) => {
        const isCmdK = (ev.key === 'k' || ev.key === 'K') && (ev.metaKey || ev.ctrlKey);
        const isSlash = ev.key === '/' && !ev.metaKey && !ev.ctrlKey && !ev.altKey;
        if (!isCmdK && !isSlash) return;
        const target = ev.target;
        const tag = target && target.tagName;
        // Don't steal "/" from someone already typing in a form field.
        if (isSlash && (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable)) {
            return;
        }
        ev.preventDefault();
        input.focus();
        input.select();
    });
})();
