/**
 * ClassUp PWA bootstrap — service worker registration + install prompt UX.
 *
 * Loaded from base.html on every page. Lightweight; no dependencies.
 *
 * What it does:
 * 1. Registers /sw.js (skipped on http:// since browsers reject SW outside HTTPS)
 * 2. Detects standalone-mode launches (iOS sets navigator.standalone, Chrome
 *    matches display-mode: standalone) and tags <body> so CSS can react
 * 3. Captures the Android `beforeinstallprompt` event and exposes a global
 *    `ClassUp.installApp()` so any UI element can trigger it
 * 4. Shows a one-time iOS install hint (since iOS Safari has no install API —
 *    only a manual "Add to Home Screen" via the Share menu)
 * 5. Notifies when a new SW version is waiting and offers a one-tap reload
 */

(function () {
  'use strict';

  // ---------------------------------------------------------------------------
  // Standalone detection — tag body so CSS can hide install prompts, etc.
  // ---------------------------------------------------------------------------
  const isStandalone =
    window.matchMedia('(display-mode: standalone)').matches ||
    window.navigator.standalone === true ||  // iOS
    document.referrer.startsWith('android-app://');

  if (isStandalone) {
    document.body.classList.add('is-standalone');
  }

  // ---------------------------------------------------------------------------
  // Service worker — register on load to avoid blocking first paint
  // ---------------------------------------------------------------------------
  if ('serviceWorker' in navigator && (location.protocol === 'https:' || location.hostname === 'localhost')) {
    window.addEventListener('load', () => {
      navigator.serviceWorker
        .register('/sw.js', { scope: '/' })
        .then((reg) => {
          // Listen for updates
          reg.addEventListener('updatefound', () => {
            const newSW = reg.installing;
            if (!newSW) return;
            newSW.addEventListener('statechange', () => {
              if (newSW.state === 'installed' && navigator.serviceWorker.controller) {
                // A new SW is waiting. Offer reload toast (non-blocking).
                showUpdateToast(reg);
              }
            });
          });
        })
        .catch((err) => console.warn('[PWA] SW registration failed:', err));

      // When the active SW changes (after skipWaiting), reload to pick up
      // the new shell. One-shot — guarded with a flag so we don't loop.
      let refreshing = false;
      navigator.serviceWorker.addEventListener('controllerchange', () => {
        if (refreshing) return;
        refreshing = true;
        window.location.reload();
      });
    });
  }

  function showUpdateToast(reg) {
    // Don't pile up — only one update toast at a time
    if (document.getElementById('pwa-update-toast')) return;

    const toast = document.createElement('div');
    toast.id = 'pwa-update-toast';
    toast.className =
      'fixed bottom-20 md:bottom-4 left-1/2 -translate-x-1/2 z-[200] ' +
      'bg-neutral-800 text-white text-sm rounded-full shadow-lg px-4 py-2.5 ' +
      'flex items-center gap-3 animate-slide-up max-w-[calc(100vw-2rem)]';
    toast.innerHTML = `
      <svg class="w-4 h-4 text-accent-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="M16 12V8a4 4 0 00-8 0v4M5 12h14l-1 8H6l-1-8z" />
      </svg>
      <span>New version available</span>
      <button id="pwa-update-btn" class="ml-2 text-accent-400 font-medium hover:text-accent-300">Reload</button>
      <button id="pwa-update-dismiss" class="text-neutral-400 hover:text-white" aria-label="Dismiss">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    `;
    document.body.appendChild(toast);

    document.getElementById('pwa-update-btn').onclick = () => {
      const waiting = reg.waiting;
      if (waiting) waiting.postMessage({ type: 'SKIP_WAITING' });
      else window.location.reload();
    };
    document.getElementById('pwa-update-dismiss').onclick = () => toast.remove();
  }

  // ---------------------------------------------------------------------------
  // Install prompt — Android / Chrome / Edge
  // ---------------------------------------------------------------------------
  let deferredInstallPrompt = null;

  window.addEventListener('beforeinstallprompt', (e) => {
    // Stop Chrome's mini-infobar so we control the timing
    e.preventDefault();
    deferredInstallPrompt = e;
    // Reveal any installer UI hiding behind .pwa-install-available
    document.body.classList.add('pwa-install-available');
  });

  window.addEventListener('appinstalled', () => {
    deferredInstallPrompt = null;
    document.body.classList.remove('pwa-install-available');
    document.body.classList.add('is-standalone');
    if (window.ClassUp && ClassUp.toast) {
      ClassUp.toast('ClassUp installed — find it on your home screen.', 'success');
    }
  });

  // Public API — hook this up to a button anywhere in the app
  window.ClassUp = window.ClassUp || {};

  /**
   * Trigger the native install prompt (Chrome/Edge/Android) or show the iOS
   * Add-to-Home-Screen banner.
   *
   * On iOS Chrome/Firefox/Edge the install path is a dead-end (those
   * browsers can't add a real PWA), so we show the banner only on iOS
   * Safari. Calling installApp() on a non-Safari iOS browser will tell the
   * user to switch to Safari instead.
   *
   * @returns {Promise<'accepted'|'dismissed'|'unavailable'>}
   */
  window.ClassUp.installApp = async function () {
    if (deferredInstallPrompt) {
      deferredInstallPrompt.prompt();
      const result = await deferredInstallPrompt.userChoice;
      deferredInstallPrompt = null;
      document.body.classList.remove('pwa-install-available');
      return result.outcome;
    }
    if (isIOS() && !isStandalone) {
      if (isIOSSafari()) {
        showIosInstallBanner({ force: true });
      } else if (window.ClassUp && ClassUp.toast) {
        ClassUp.toast('To install, open ClassUp in Safari and try again.', 'info');
      }
      return 'unavailable';
    }
    if (window.ClassUp && ClassUp.toast) {
      ClassUp.toast('Already installed, or not supported by this browser.', 'info');
    }
    return 'unavailable';
  };

  // canInstall: true on Android with a deferred prompt OR on iOS Safari that
  // isn't already in standalone. Hide the install button everywhere else.
  window.ClassUp.canInstall = () =>
    deferredInstallPrompt !== null || (isIOSSafari() && !isStandalone);
  window.ClassUp.isStandalone = () => isStandalone;

  // ---------------------------------------------------------------------------
  // Platform detection — iOS includes iPadOS, which lies and reports MacIntel
  // ---------------------------------------------------------------------------

  /** Any iOS device, including iPadOS 13+ which spoofs as MacIntel. */
  function isIOS() {
    const ua = navigator.userAgent;
    if (/iPad|iPhone|iPod/.test(ua) && !window.MSStream) return true;
    // iPadOS 13+ on iPad Pro masquerades as macOS — distinguish via touch points
    if (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1) return true;
    return false;
  }

  /** Real Safari on iOS — not Chrome (CriOS), Firefox (FxiOS), Edge (EdgiOS),
   *  or Opera (OPiOS). Only Safari can install a PWA on iOS; everything else
   *  is a WebKit wrapper that will *say* "Add to Home Screen" but produce a
   *  bookmark, not a real PWA — so we don't nag those users. */
  function isIOSSafari() {
    if (!isIOS()) return false;
    const ua = navigator.userAgent;
    return !/CriOS|FxiOS|EdgiOS|OPiOS|YaBrowser|UCBrowser/i.test(ua);
  }

  // ---------------------------------------------------------------------------
  // iOS install banner — bottom-anchored, three illustrated steps, two-tier
  // dismiss. Shown:
  //   - manually via ClassUp.installApp() (force=true bypasses snooze)
  //   - automatically on the user's 3rd visit, then snoozed if dismissed
  // ---------------------------------------------------------------------------

  // Storage keys — namespaced so a future "wipe all PWA prefs" is easy
  const HINT_SNOOZED_UNTIL = 'classup_ios_hint_snoozed_until'; // unix ms
  const HINT_NEVER = 'classup_ios_hint_never';                  // '1' once set
  const HINT_VISITS = 'classup_ios_visits';
  const SNOOZE_DAYS = 14;
  const AUTO_SHOW_VISIT = 3;

  function isHintSilenced() {
    try {
      if (localStorage.getItem(HINT_NEVER) === '1') return true;
      const until = parseInt(localStorage.getItem(HINT_SNOOZED_UNTIL) || '0', 10);
      if (until && Date.now() < until) return true;
    } catch (e) { /* private browsing — fall through */ }
    return false;
  }

  function snoozeHint(days) {
    try {
      const until = Date.now() + days * 24 * 60 * 60 * 1000;
      localStorage.setItem(HINT_SNOOZED_UNTIL, String(until));
    } catch (e) {}
  }

  function silenceHintForever() {
    try { localStorage.setItem(HINT_NEVER, '1'); } catch (e) {}
  }

  /**
   * @param {{force?: boolean}} opts - force=true bypasses the snooze + never
   *        flags (used when the user explicitly taps "Install ClassUp").
   */
  function showIosInstallBanner(opts) {
    opts = opts || {};
    if (document.getElementById('ios-install-banner')) return;
    if (!opts.force && isHintSilenced()) return;

    const banner = document.createElement('div');
    banner.id = 'ios-install-banner';
    banner.setAttribute('role', 'dialog');
    banner.setAttribute('aria-modal', 'false');
    banner.setAttribute('aria-labelledby', 'ios-install-title');
    // Sits above the mobile bottom nav (z-50) but below toasts (z-100)
    banner.className =
      'fixed left-0 right-0 z-[60] px-3 pointer-events-none ' +
      // Sit above the bottom nav (h-14) on mobile, otherwise float above the
      // home indicator. Desktop mode (md:) is unlikely on iOS but handle it.
      'bottom-[calc(3.5rem+env(safe-area-inset-bottom))] md:bottom-4';
    banner.innerHTML = `
      <div class="pointer-events-auto mx-auto max-w-md bg-white rounded-2xl shadow-2xl border border-neutral-200 overflow-hidden animate-slide-up">
        <div class="p-4">
          <div class="flex items-start gap-3 mb-3">
            <div class="flex-shrink-0 w-10 h-10 rounded-lg bg-primary-50 flex items-center justify-center">
              <img src="/static/img/icons/icon-192.png" alt="" class="w-8 h-8 rounded-md">
            </div>
            <div class="flex-1 min-w-0">
              <h3 id="ios-install-title" class="text-sm font-semibold text-neutral-800">
                Install ClassUp on your iPhone
              </h3>
              <p class="text-xs text-neutral-500 mt-0.5">
                Add to your home screen for fullscreen access and faster loading.
              </p>
            </div>
            <button id="ios-banner-close" type="button"
                    class="flex-shrink-0 -m-1.5 p-1.5 text-neutral-400 hover:text-neutral-600"
                    aria-label="Dismiss">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
                <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          <ol class="space-y-2 text-xs text-neutral-700 pl-1">
            <li class="flex items-center gap-2">
              <span class="flex-shrink-0 w-5 h-5 rounded-full bg-primary-100 text-primary-700 flex items-center justify-center font-semibold">1</span>
              <span>Tap the
                <svg class="inline w-3.5 h-3.5 align-text-bottom mx-0.5 text-primary-600" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M9 8.25H7.5a2.25 2.25 0 00-2.25 2.25v9a2.25 2.25 0 002.25 2.25h9a2.25 2.25 0 002.25-2.25v-9a2.25 2.25 0 00-2.25-2.25H15M9 12l3 3m0 0l3-3m-3 3V2.25" />
                </svg>
                <strong>Share</strong> icon below.
              </span>
            </li>
            <li class="flex items-center gap-2">
              <span class="flex-shrink-0 w-5 h-5 rounded-full bg-primary-100 text-primary-700 flex items-center justify-center font-semibold">2</span>
              <span>Tap <strong>Add to Home Screen</strong>
                <svg class="inline w-3.5 h-3.5 align-text-bottom mx-0.5 text-primary-600" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
                </svg>.
              </span>
            </li>
            <li class="flex items-center gap-2">
              <span class="flex-shrink-0 w-5 h-5 rounded-full bg-primary-100 text-primary-700 flex items-center justify-center font-semibold">3</span>
              <span>Tap <strong>Add</strong> in the top-right.</span>
            </li>
          </ol>

          <div class="mt-3 pt-3 border-t border-neutral-100 flex items-center gap-2">
            <button id="ios-banner-snooze" type="button"
                    class="flex-1 px-3 py-2 text-xs font-medium text-neutral-700 bg-neutral-100 rounded-lg hover:bg-neutral-200">
              Not now
            </button>
            <button id="ios-banner-never" type="button"
                    class="flex-1 px-3 py-2 text-xs font-medium text-neutral-500 bg-white border border-neutral-200 rounded-lg hover:bg-neutral-50">
              Don't show again
            </button>
          </div>
        </div>
      </div>
    `;
    document.body.appendChild(banner);

    const remove = () => banner.remove();
    banner.querySelector('#ios-banner-close').onclick = () => {
      snoozeHint(SNOOZE_DAYS);
      remove();
    };
    banner.querySelector('#ios-banner-snooze').onclick = () => {
      snoozeHint(SNOOZE_DAYS);
      remove();
    };
    banner.querySelector('#ios-banner-never').onclick = () => {
      silenceHintForever();
      remove();
    };
  }

  // Auto-show on iOS Safari only — never iOS Chrome (would be a dead-end)
  if (isIOSSafari() && !isStandalone && !isHintSilenced()) {
    try {
      const visits = parseInt(localStorage.getItem(HINT_VISITS) || '0', 10) + 1;
      localStorage.setItem(HINT_VISITS, String(visits));
      if (visits === AUTO_SHOW_VISIT) {
        // Small delay so the page settles first
        setTimeout(() => showIosInstallBanner(), 1500);
      }
    } catch (e) { /* private browsing — silently skip */ }
  }
})();
